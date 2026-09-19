import hashlib
import json
import tempfile
import time
from decimal import Decimal
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.audio.models import Transcription
from apps.projects.models import Project
from apps.quizzes.models import Quiz
from apps.sources.capabilities import get_source_character_capabilities
from apps.sources.models import StudentSource, StudentSourceCollection
from apps.subjects.models import EducationStage, Subject
from apps.subscriptions.models import UsageLedgerEntry
from apps.subscriptions.services import (
    ensure_default_plans,
    get_or_create_user_subscription,
    reserve_character_request,
)

from .client import AIServiceClient, AIServiceError
from .error_codes import ErrorCode
from .materializers import _create_derived_text_source, materialize_job
from .models import AIJob, AIJobDispatchOutbox, AIWebhookEvent
from .security import InternalAuthenticationError, make_service_signature, verify_internal_request
from .services import (
    AI_STAGE_MAP,
    MAX_SOURCES_PER_JOB,
    AIRequestError,
    build_khota_job_input,
    build_service_payload,
    complete_job,
    content_sha256,
    create_ai_job,
    fail_job,
    public_progress_stage,
    record_ai_stage,
    resolve_job_sources,
    update_job_progress,
)
from .tasks import dispatch_ai_job

User = get_user_model()


@override_settings(AI_SERVICE_ENABLED=True, AI_SERVICE_BASE_URL='https://ai.example.test')
class AIServiceClientSafetyTests(SimpleTestCase):
    def test_job_status_and_cancel_targets_include_signed_user_scope(self):
        client = AIServiceClient()
        self.assertEqual(
            client._job_target('job-1', 'user 1'),
            f"{settings.AI_SERVICE_JOBS_PATH}/job-1?user_id=user%201",
        )
        self.assertEqual(
            client._job_target('job-1', 'user 1', cancel=True),
            f"{settings.AI_SERVICE_JOBS_PATH}/job-1/cancel?user_id=user%201",
        )

    @patch('apps.ai_integration.client.requests.Session.request')
    def test_upstream_error_does_not_expose_upstream_message(self, request):
        response = Mock(status_code=500)
        response.json.return_value = {
            'success': False,
            'error': {'message': 'database password=do-not-leak', 'code': 'internal_exception'},
        }
        request.return_value = response

        with self.assertRaises(AIServiceError) as raised:
            AIServiceClient().create_job({'task_type': 'fahes_generate_quiz'})

        # "internal_exception" isn't one of our shared codes, so a 500 maps
        # to the generic retryable provider_unavailable bucket rather than
        # propagating an arbitrary upstream string.
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertNotIn('password', str(raised.exception))

    @patch('apps.ai_integration.client.requests.Session.request')
    def test_upstream_rate_limit_maps_to_provider_rate_limited(self, request):
        response = Mock(status_code=429)
        response.json.return_value = {'success': False, 'error': {'code': 'too_many_requests'}}
        request.return_value = response

        with self.assertRaises(AIServiceError) as raised:
            AIServiceClient().create_job({'task_type': 'fahes_generate_quiz'})

        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_RATE_LIMITED)
        self.assertTrue(raised.exception.retryable)

    @patch('apps.ai_integration.client.requests.Session.request')
    def test_upstream_known_code_is_propagated_verbatim(self, request):
        response = Mock(status_code=422)
        response.json.return_value = {'success': False, 'error': {'code': 'source_forbidden'}}
        request.return_value = response

        with self.assertRaises(AIServiceError) as raised:
            AIServiceClient().create_job({'task_type': 'fahes_generate_quiz'})

        self.assertEqual(raised.exception.code, ErrorCode.SOURCE_FORBIDDEN)
        self.assertFalse(raised.exception.retryable)


@override_settings(
    ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'],
    AI_SERVICE_ENABLED=True,
    BARAQ_SERVICE_ID='baraq-django',
    BARAQ_HMAC_CURRENT_KEY_ID='django-current',
    BARAQ_HMAC_KEYS_JSON='{"django-current":"test-hmac-secret"}',
    BARAQ_HMAC_ALLOWED_SERVICES=['baraq-ai-service'],
    BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS=300,
    BARAQ_HMAC_NONCE_TTL_SECONDS=600,
)
class AIIntegrationApiTests(APITestCase):
    def setUp(self):
        self.media_override = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        self.media_override.enable()
        self.user = User.objects.create_user(email='ai@example.com', password='StrongPass123!', full_name='AI User')
        self.other = User.objects.create_user(email='other-ai@example.com', password='StrongPass123!', full_name='Other AI')
        stage = EducationStage.objects.create(name='Secondary', order=1)
        self.subject = Subject.objects.create(name='Physics', education_stage=stage, grade_level='12')
        # Blueprint 01_BACKEND.md §3.1: every source-based AI job now requires
        # a project (inherited from its source/collection when not explicit).
        self.project = Project.objects.create(owner=self.user, title='Physics Project')
        self.source = StudentSource.objects.create(
            user=self.user,
            project=self.project,
            subject=self.subject,
            title='Physics notes',
            source_type=StudentSource.SourceType.TEXT,
            file=SimpleUploadedFile('physics.txt', b'Newton laws', content_type='text/plain'),
            original_filename='physics.txt',
            file_size=11,
            mime_type='text/plain',
            extension='txt',
            extracted_text='Newton laws',
            status=StudentSource.Status.READY,
        )

    def tearDown(self):
        cache.clear()
        self.media_override.disable()

    def internal_headers(self, method, target, body=b'', *, nonce='nonce-for-test-0001'):
        headers = make_service_signature(
            method=method,
            target=target,
            body=body,
            timestamp=int(time.time()),
            nonce=nonce,
            service='baraq-ai-service',
            key_id='django-current',
        )
        return {f"HTTP_{name.upper().replace('-', '_')}": value for name, value in headers.items()}

    def authenticate(self):
        self.client.force_authenticate(user=self.user)

    def test_job_creation_is_idempotent(self):
        self.authenticate()
        payload = {
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source': self.source.id,
            'subject': self.subject.id,
            'parameters': {'questions_count': 5},
        }
        first = self.client.post(reverse('ai-job-list'), payload, format='json')
        second = self.client.post(reverse('ai-job-list'), payload, format='json')
        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data['public_id'], second.data['public_id'])
        self.assertEqual(AIJob.objects.filter(user=self.user).count(), 1)
        self.assertEqual(first.data['input_payload']['source_ids'], [str(self.source.id)])
        self.assertEqual(first.data['input_payload']['question_count'], 5)

    def test_user_cannot_submit_another_users_source(self):
        other_source = StudentSource.objects.create(
            user=self.other,
            subject=self.subject,
            title='Other',
            source_type=StudentSource.SourceType.TEXT,
            file='student_sources/other.txt',
            original_filename='other.txt',
            file_size=5,
            mime_type='text/plain',
            extension='txt',
            status=StudentSource.Status.READY,
        )
        self.authenticate()
        response = self.client.post(
            reverse('ai-job-list'),
            {'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ, 'source': other_source.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_internal_manifest_requires_hmac_v2_signature(self):
        missing = self.client.get(reverse('ai-source-manifest', args=[self.source.id]))
        self.assertEqual(missing.status_code, status.HTTP_403_FORBIDDEN)
        target = f'/api/internal/v1/ai/sources/{self.source.id}/manifest/?user_id={self.user.id}'
        allowed = self.client.get(
            f"{reverse('ai-source-manifest', args=[self.source.id])}?user_id={self.user.id}",
            **self.internal_headers('GET', target),
        )
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(allowed.data['source_id'], str(self.source.id))
        self.assertEqual(allowed.data['owner_user_id'], str(self.user.id))
        # Baraq_MD_Blueprint 02_AI_PLATFORM.md §3.2: the AI service verifies
        # a source's project against the job's own project_id, so the
        # manifest must carry it.
        self.assertEqual(allowed.data['project_id'], str(self.project.id))
        self.assertEqual(allowed.data['size_bytes'], self.source.file_size)
        self.assertEqual(len(allowed.data['content_sha256']), 64)

    def test_internal_manifest_validates_requested_owner(self):
        target = f'/api/internal/v1/ai/sources/{self.source.id}/manifest/?user_id={self.user.id}'
        allowed = self.client.get(
            reverse('ai-source-manifest', args=[self.source.id]),
            {'user_id': self.user.id},
            **self.internal_headers('GET', target),
        )
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)

        denied_target = f'/api/internal/v1/ai/sources/{self.source.id}/manifest/?user_id={self.other.id}'
        denied = self.client.get(
            reverse('ai-source-manifest', args=[self.source.id]),
            {'user_id': self.other.id},
            **self.internal_headers('GET', denied_target, nonce='nonce-for-test-0002'),
        )
        self.assertEqual(denied.status_code, status.HTTP_404_NOT_FOUND)

    def test_internal_manifest_rejects_replayed_nonce_and_tampered_query(self):
        target = f'/api/internal/v1/ai/sources/{self.source.id}/manifest/?user_id={self.user.id}'
        headers = self.internal_headers('GET', target, nonce='nonce-for-test-0003')
        allowed = self.client.get(f"{reverse('ai-source-manifest', args=[self.source.id])}?user_id={self.user.id}", **headers)
        replayed = self.client.get(f"{reverse('ai-source-manifest', args=[self.source.id])}?user_id={self.user.id}", **headers)
        tampered = self.client.get(
            f"{reverse('ai-source-manifest', args=[self.source.id])}?user_id={self.other.id}",
            **self.internal_headers('GET', target, nonce='nonce-for-test-0004'),
        )
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(replayed.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(tampered.status_code, status.HTTP_403_FORBIDDEN)

    def test_shared_hmac_v2_vector_matches_ai_service(self):
        headers = make_service_signature(
            method='POST',
            target='/api/ai/v1/jobs?b=2&a=1',
            body=b'{"a":1}',
            timestamp=1_700_000_000,
            nonce='nonce-for-test-0001',
            service='baraq-django',
            key_id='django-current',
        )
        self.assertEqual(
            headers['X-Content-SHA256'],
            '015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862',
        )
        self.assertEqual(
            headers['X-Baraq-Signature'],
            '5e41ddc5b38525cac1fe8c1e44d2d5fbbcae5a0a2788dba6e9021c76eb276f99',
        )

    def test_service_payload_is_the_strict_v2_contract(self):
        self.authenticate()
        created = self.client.post(
            reverse('ai-job-list'),
            {'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ, 'source': self.source.id},
            format='json',
        )
        job = AIJob.objects.get(public_id=created.data['public_id'])
        payload = build_service_payload(job)
        self.assertEqual(payload['contract_version'], '2.0')
        self.assertEqual(payload['task_type'], AIJob.TaskType.FAHES_GENERATE_QUIZ)
        self.assertEqual(payload['input']['source_ids'], [str(self.source.id)])
        self.assertEqual(payload['model_policy'], {'tier': 'balanced', 'allow_fallback': True})
        self.assertEqual(payload['source_ids'], [str(self.source.id)])
        self.assertEqual(len(payload['source_versions'][str(self.source.id)]), 64)
        self.assertEqual(
            set(payload),
            {
                'contract_version', 'client_job_id', 'user_id', 'project_id',
                'task_type', 'source_ids', 'source_versions', 'input', 'model_policy', 'trace_context',
            },
        )

    def test_completed_job_requires_a_materialized_result_in_the_database(self):
        with self.assertRaises(IntegrityError):
            AIJob.objects.create(
                user=self.user,
                character=AIJob.Character.FAHES,
                task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
                status=AIJob.Status.COMPLETED,
                idempotency_key='completed-without-result',
            )

    def test_progress_updates_cannot_mark_a_job_completed(self):
        job = AIJob.objects.create(
            user=self.user,
            character=AIJob.Character.FAHES,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            status=AIJob.Status.QUEUED,
            idempotency_key='progress-does-not-complete',
        )
        with self.assertRaises(ValidationError):
            update_job_progress(job, AIJob.Status.COMPLETED)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.QUEUED)

    def test_completion_materializes_once_then_commits_usage_and_notifies_once(self):
        job = AIJob.objects.create(
            user=self.user,
            source=self.source,
            subject=self.subject,
            character=AIJob.Character.FAHES,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            status=AIJob.Status.QUEUED,
            idempotency_key='complete-job-once',
        )
        reserve_character_request(self.user, job.character, job=job)
        job.credits_reserved = True
        job.save(update_fields=['credits_reserved', 'updated_at'])

        output = {
            'questions': [{
                'question': 'What does Newton\'s first law describe?',
                'choices': ['Inertia', 'Photosynthesis'],
                'correct_answer_index': 0,
            }],
        }
        completed = complete_job(job, output)
        repeated = complete_job(completed, output)

        self.assertEqual(completed.status, AIJob.Status.COMPLETED)
        self.assertEqual(completed.result_type, 'quiz')
        self.assertEqual(repeated.pk, completed.pk)
        self.assertEqual(
            UsageLedgerEntry.objects.filter(
                user=self.user,
                idempotency_key=job.idempotency_key,
                operation=UsageLedgerEntry.Operation.COMMIT,
            ).count(),
            1,
        )
        from apps.notifications.models import Notification

        self.assertEqual(
            Notification.objects.filter(
                user=self.user,
                idempotency_key=f'ai-job:{job.public_id}:completed',
            ).count(),
            1,
        )

    def test_sada_rejects_collection_even_when_it_contains_audio(self):
        from apps.sources.models import StudentSourceCollection

        collection = StudentSourceCollection.objects.create(
            user=self.user, project=self.project, name='Audio folder'
        )
        audio = StudentSource.objects.create(
            user=self.user,
            project=self.project,
            title='Lecture recording',
            source_type=StudentSource.SourceType.AUDIO,
            file=SimpleUploadedFile('lecture.mp3', b'ID3audio', content_type='audio/mpeg'),
            original_filename='lecture.mp3',
            file_size=8,
            mime_type='audio/mpeg',
            extension='mp3',
            collection=collection,
            status=StudentSource.Status.UPLOADED,
        )
        self.authenticate()
        response = self.client.post(
            reverse('ai-job-list'),
            {'task_type': AIJob.TaskType.SADA_TRANSCRIBE_AUDIO, 'collection': collection.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        accepted = self.client.post(
            reverse('ai-job-list'),
            {'task_type': AIJob.TaskType.SADA_TRANSCRIBE_AUDIO, 'source': audio.id},
            format='json',
        )
        # The test account does not have the Sada subscription feature, so a
        # 403 proves the audio source passed request validation and reached
        # the entitlement layer (rather than being rejected as malformed).
        self.assertEqual(accepted.status_code, status.HTTP_403_FORBIDDEN)

    def test_feedback_requires_completed_job(self):
        self.authenticate()
        create = self.client.post(
            reverse('ai-job-list'),
            {'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ, 'source': self.source.id},
            format='json',
        )
        response = self.client.post(
            reverse('ai-job-feedback', args=[create.data['public_id']]),
            {'rating': 5, 'is_helpful': True},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_unsigned_webhook_is_rejected(self):
        response = self.client.post(
            reverse('ai-webhook-jobs'),
            {'event_id': 'event-1', 'job_id': 'job-1', 'status': 'completed'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def _post_signed_webhook(self, payload, *, nonce):
        body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
        target = reverse('ai-webhook-jobs')
        headers = self.internal_headers('POST', target, body, nonce=nonce)
        return self.client.post(target, data=body, content_type='application/json', **headers)

    def _quiz_ready_job(self, idempotency_key):
        job = AIJob.objects.create(
            user=self.user,
            source=self.source,
            subject=self.subject,
            character=AIJob.Character.FAHES,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            status=AIJob.Status.SUBMITTED,
            external_job_id=f'external-{idempotency_key}',
            idempotency_key=idempotency_key,
        )
        reserve_character_request(self.user, job.character, job=job)
        job.credits_reserved = True
        job.save(update_fields=['credits_reserved', 'updated_at'])
        return job

    def test_webhook_happy_path_completes_and_captures_telemetry(self):
        job = self._quiz_ready_job('webhook-happy-path')
        payload = {
            'event_id': 'evt-happy-1',
            'event_type': 'job.completed',
            'job_id': job.external_job_id,
            'status': 'completed',
            'result': {
                'questions': [{
                    'question': "What does Newton's first law describe?",
                    'choices': ['Inertia', 'Photosynthesis'],
                    'correct_answer_index': 0,
                }],
            },
            'metadata': {
                'request_id': str(job.request_id) if job.request_id else 'req-happy-1',
                'quality': {'quality_score': 0.92, 'groundedness_score': 0.81},
                'usage': {
                    'input_tokens': 512,
                    'output_tokens': 128,
                    'total_tokens': 640,
                    'estimated_cost_usd': 0.0123,
                },
                'provider': {
                    'account': 'gemini_primary',
                    'model': 'gemini-2.5-flash',
                    'response_id': 'resp-happy-1',
                },
                'prompt': {'name': 'fahes_generate_quiz', 'version': 'fahes.generate_quiz@2.3.1'},
                'warnings': [],
                'security_flags': [],
            },
        }
        response = self._post_signed_webhook(payload, nonce='webhook-nonce-happy-1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.COMPLETED)
        self.assertEqual(job.result_type, 'quiz')
        self.assertEqual(job.provider_account, 'gemini_primary')
        self.assertEqual(job.model_name, 'gemini-2.5-flash')
        self.assertEqual(job.prompt_version, 'fahes.generate_quiz@2.3.1')
        self.assertEqual(job.input_tokens, 512)
        self.assertEqual(job.output_tokens, 128)
        self.assertEqual(job.cost_usd, Decimal('0.0123'))
        self.assertEqual(job.security_flags, [])
        self.assertEqual(
            job.service_metadata.get('webhook', {}).get('provider', {}).get('account'), 'gemini_primary'
        )
        event = AIWebhookEvent.objects.get(event_id='evt-happy-1')
        self.assertTrue(event.processed)

        # Duplicate delivery of the same event must not reprocess or
        # re-materialize.
        replay = self._post_signed_webhook(payload, nonce='webhook-nonce-happy-2')
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertTrue(replay.data.get('duplicate'))
        self.assertEqual(Quiz.objects.filter(ai_job=job).count(), 1)

    def test_webhook_rejects_a_reused_event_id_with_a_different_payload(self):
        job = self._quiz_ready_job('webhook-idempotency-conflict')
        first_payload = {
            'event_id': 'evt-conflict-1',
            'job_id': job.external_job_id,
            'status': 'processing',
        }
        second_payload = {
            'event_id': 'evt-conflict-1',
            'job_id': job.external_job_id,
            'status': 'failed',
            'error_message': 'a different, conflicting event body',
        }
        first = self._post_signed_webhook(first_payload, nonce='webhook-nonce-conflict-1')
        second = self._post_signed_webhook(second_payload, nonce='webhook-nonce-conflict-2')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second.data.get('code'), ErrorCode.IDEMPOTENCY_CONFLICT)
        job.refresh_from_db()
        self.assertNotEqual(job.status, AIJob.Status.FAILED)

    def test_webhook_rejects_an_unrecognized_status(self):
        job = self._quiz_ready_job('webhook-unrecognized-status')
        payload = {'event_id': 'evt-unknown-status', 'job_id': job.external_job_id, 'status': 'teleporting'}
        response = self._post_signed_webhook(payload, nonce='webhook-nonce-unknown-1')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('code'), ErrorCode.INVALID_CONTRACT)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.SUBMITTED)

    def test_webhook_failure_maps_a_known_remote_code_onto_the_job(self):
        job = self._quiz_ready_job('webhook-failure-code')
        payload = {
            'event_id': 'evt-failure-1',
            'job_id': job.external_job_id,
            'status': 'failed',
            'error_code': 'source_not_found',
            'error_message': 'The source was deleted before processing.',
        }
        response = self._post_signed_webhook(payload, nonce='webhook-nonce-failure-1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.FAILED)
        self.assertEqual(job.error_code, ErrorCode.SOURCE_NOT_FOUND)

    def test_webhook_failure_preserves_actionable_code_but_not_remote_diagnostics(self):
        job = self._quiz_ready_job('webhook-actionable-failure')
        payload = {
            'event_id': 'evt-failure-actionable-1',
            'job_id': job.external_job_id,
            'status': 'failed',
            'error_code': 'pdf_ocr_required',
            'error_message': 'postgresql://user:secret@private-db plus source text',
            'retryable': True,
        }

        response = self._post_signed_webhook(payload, nonce='webhook-nonce-failure-actionable-1')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.FAILED)
        self.assertEqual(job.error_code, ErrorCode.PDF_OCR_REQUIRED)
        self.assertEqual(
            job.error_message,
            'This PDF does not contain extractable text and requires OCR.',
        )
        self.assertNotIn('private-db', job.error_message)
        self.assertEqual(
            job.service_metadata['failure'],
            {'code': 'pdf_ocr_required', 'retryable': False},
        )

        self.authenticate()
        detail = self.client.get(reverse('ai-job-detail', args=[job.public_id]))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data['error_code'], 'pdf_ocr_required')
        self.assertEqual(detail.data['error_message'], job.error_message)

    def test_unknown_remote_failure_is_reduced_to_safe_generic_error(self):
        job = self._quiz_ready_job('webhook-unknown-failure')
        payload = {
            'event_id': 'evt-failure-unknown-1',
            'job_id': job.external_job_id,
            'status': 'failed',
            'error_code': 'RuntimeError',
            'error_message': 'api_key=do-not-leak',
        }

        response = self._post_signed_webhook(payload, nonce='webhook-nonce-failure-unknown-1')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job.refresh_from_db()
        self.assertEqual(job.error_code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertNotIn('do-not-leak', job.error_message)

    def test_late_failure_cannot_overwrite_completed_job(self):
        job = self._quiz_ready_job('late-failure-after-completion')
        complete_job(job, {'questions': [{
            'question': 'Which fact is in the source?',
            'choices': ['Seven stages', 'Nine stages'],
            'correct_answer_index': 0,
        }]})

        unchanged = fail_job(
            job,
            AIServiceError(
                'provider diagnostic',
                code=ErrorCode.PROVIDER_TIMEOUT,
                retryable=True,
            ),
        )

        self.assertEqual(unchanged.status, AIJob.Status.COMPLETED)
        self.assertEqual(unchanged.error_code, '')

    def test_feedback_captures_provider_model_prompt_version_snapshot(self):
        job = self._quiz_ready_job('feedback-snapshot')
        complete_job(job, {'questions': [{
            'question': "What does Newton's first law describe?",
            'choices': ['Inertia', 'Photosynthesis'],
            'correct_answer_index': 0,
        }]}, {
            'provider': {'account': 'openai', 'model': 'gpt-5-mini'},
            'prompt': {'version': 'fahes@1.0.0'},
        })
        self.authenticate()
        response = self.client.post(
            reverse('ai-job-feedback', args=[job.public_id]),
            {'rating': 5, 'is_helpful': True},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['provider_snapshot'], 'openai')
        self.assertEqual(response.data['model_snapshot'], 'gpt-5-mini')
        self.assertEqual(response.data['prompt_version_snapshot'], 'fahes@1.0.0')

    def test_sada_creates_a_derived_text_source_usable_by_other_characters(self):
        # Sada is a premium-only character (see DEFAULT_PLANS['free']['features']),
        # so this user needs an active premium subscription before reserving it.
        plans = ensure_default_plans()
        subscription = get_or_create_user_subscription(self.user)
        subscription.plan = plans['premium']
        subscription.save(update_fields=['plan', 'updated_at'])

        audio = StudentSource.objects.create(
            user=self.user,
            subject=self.subject,
            title='Lecture recording',
            source_type=StudentSource.SourceType.AUDIO,
            file=SimpleUploadedFile('lecture.mp3', b'ID3audio', content_type='audio/mpeg'),
            original_filename='lecture.mp3',
            file_size=8,
            mime_type='audio/mpeg',
            extension='mp3',
            status=StudentSource.Status.READY,
        )
        job = AIJob.objects.create(
            user=self.user,
            source=audio,
            subject=self.subject,
            character=AIJob.Character.SADA,
            task_type=AIJob.TaskType.SADA_TRANSCRIBE_AUDIO,
            status=AIJob.Status.SUBMITTED,
            idempotency_key='sada-derived-source',
        )
        reserve_character_request(self.user, job.character, job=job)
        job.credits_reserved = True
        job.save(update_fields=['credits_reserved', 'updated_at'])

        completed = complete_job(job, {
            'full_transcript': 'raw transcript text',
            'cleaned_transcript': 'cleaned lecture transcript about Newton laws',
            'language': 'ar',
        })

        derived_source_id = completed.result_payload.get('derived_source_id')
        self.assertIsNotNone(derived_source_id)
        derived = StudentSource.objects.get(pk=derived_source_id)
        self.assertEqual(derived.source_type, StudentSource.SourceType.TEXT)
        self.assertEqual(derived.status, StudentSource.Status.READY)
        self.assertEqual(derived.extracted_text, 'cleaned lecture transcript about Newton laws')
        self.assertEqual(derived.metadata.get('derived_from'), 'sada')
        self.assertEqual(derived.user_id, self.user.id)

    def test_khota_defaults_weak_topics_from_the_latest_rasheed_recommendation(self):
        from apps.analytics.models import StudentRecommendation

        rasheed_job = AIJob.objects.create(
            user=self.user,
            subject=self.subject,
            character=AIJob.Character.RASHEED,
            task_type=AIJob.TaskType.RASHEED_RECOMMENDATIONS,
            status=AIJob.Status.SUBMITTED,
            idempotency_key='rasheed-for-khota-link',
        )
        StudentRecommendation.objects.create(
            user=self.user,
            subject=self.subject,
            ai_job=rasheed_job,
            title='Rasheed recommendation',
            summary='...',
            weaknesses=['Newton\'s laws', 'Thermodynamics'],
            recommendations=[],
        )
        built_input = build_khota_job_input(
            subject=self.subject,
            user=self.user,
            project=None,
            input_payload={
                'start_date': '2026-09-01',
                'end_date': '2026-09-03',
                'daily_available_minutes': 60,
            },
            parameters={},
        )
        self.assertEqual(built_input['weak_topics'], ["Newton's laws", 'Thermodynamics'])


@override_settings(
    BARAQ_HMAC_CURRENT_KEY_ID='django-current',
    BARAQ_HMAC_KEYS_JSON='{"django-current":"test-hmac-secret-at-least-32-chars-long"}',
    BARAQ_HMAC_ALLOWED_SERVICES=['baraq-ai-service'],
    BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS=300,
    BARAQ_HMAC_NONCE_TTL_SECONDS=600,
)
class HMACV2InboundVerificationTests(SimpleTestCase):
    """Direct, low-level tests of ``verify_internal_request`` -- the same
    function that gates every AI -> Django call (webhooks, source manifests).
    Complements the higher-level view tests above (which already cover
    missing signatures, tampering, and basic replay) with two properties
    those don't exercise: clock-skew rejection and fail-closed behavior when
    the nonce/replay store (Redis, in production) is unavailable."""

    def _signed_request(self, *, timestamp, nonce, path='/internal/v1/ai/ping/', body=b'{}'):
        headers = make_service_signature(
            method='POST', target=path, body=body, timestamp=timestamp, nonce=nonce,
            service='baraq-ai-service', key_id='django-current',
        )
        return RequestFactory().generic(
            'POST', path, data=body, content_type='application/json',
            **{f"HTTP_{name.upper().replace('-', '_')}": value for name, value in headers.items()},
        )

    def test_within_clock_skew_window_is_accepted(self):
        request = self._signed_request(timestamp=int(time.time()), nonce='skew-ok-nonce-0001')
        authenticated = verify_internal_request(request)
        self.assertEqual(authenticated.service, 'baraq-ai-service')

    def test_timestamp_too_old_is_rejected_as_expired(self):
        stale_timestamp = int(time.time()) - 301  # 1 second past the 300s max skew
        request = self._signed_request(timestamp=stale_timestamp, nonce='skew-old-nonce-0001')
        with self.assertRaises(InternalAuthenticationError) as ctx:
            verify_internal_request(request)
        self.assertEqual(ctx.exception.code, 'expired_signature')

    def test_timestamp_too_far_in_the_future_is_rejected_as_expired(self):
        future_timestamp = int(time.time()) + 301
        request = self._signed_request(timestamp=future_timestamp, nonce='skew-future-nonce-0001')
        with self.assertRaises(InternalAuthenticationError) as ctx:
            verify_internal_request(request)
        self.assertEqual(ctx.exception.code, 'expired_signature')

    def test_fails_closed_when_the_replay_store_is_unavailable(self):
        """If the nonce cache (Redis in production) is down, the request must
        be REJECTED, not silently allowed through as if replay-protection
        were optional."""

        request = self._signed_request(timestamp=int(time.time()), nonce='redis-down-nonce-0001')
        with (
            patch('apps.ai_integration.security.cache.add', side_effect=ConnectionError('redis unreachable')),
            self.assertRaises(InternalAuthenticationError) as ctx,
        ):
            verify_internal_request(request)
        self.assertEqual(ctx.exception.code, 'replay_store_unavailable')

    def test_reusing_the_same_nonce_twice_is_rejected(self):
        request_one = self._signed_request(timestamp=int(time.time()), nonce='reused-nonce-000001')
        request_two = self._signed_request(timestamp=int(time.time()), nonce='reused-nonce-000001')
        verify_internal_request(request_one)
        with self.assertRaises(InternalAuthenticationError) as ctx:
            verify_internal_request(request_two)
        self.assertEqual(ctx.exception.code, 'replay_detected')


class SadaDerivedSourceTests(APITestCase):
    """The Sada learning loop: audio -> transcript -> a source Fahes and
    Kholasa can actually select.

    The derived source used to be created with `extracted_text` but no
    `file`. Every other source in the system is bytes plus a content hash,
    and `content_sha256` raises Http404 on a source without a file -- so the
    first downstream job died at dispatch, the outbox retried it to
    exhaustion, and the learner was told "provider unavailable" for something
    no provider was ever asked about.
    """

    def setUp(self):
        self.media_override = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        self.media_override.enable()
        self.user = User.objects.create_user(
            email='sada@example.com', password='StrongPass123!', full_name='Sada User'
        )
        stage = EducationStage.objects.create(name='Secondary', order=1)
        self.subject = Subject.objects.create(
            name='History', education_stage=stage, grade_level='12'
        )
        self.project = Project.objects.create(owner=self.user, title='History Project')
        self.collection = StudentSourceCollection.objects.create(
            user=self.user, project=self.project, name='Lectures'
        )
        self.audio = StudentSource.objects.create(
            user=self.user,
            project=self.project,
            subject=self.subject,
            collection=self.collection,
            title='Lecture recording',
            source_type=StudentSource.SourceType.AUDIO,
            file=SimpleUploadedFile(
                'lecture.mp3', b'ID3' + b'\x00' * 40, content_type='audio/mpeg'
            ),
            original_filename='lecture.mp3',
            file_size=43,
            mime_type='audio/mpeg',
            extension='mp3',
            status=StudentSource.Status.UPLOADED,
        )
        self.job = AIJob.objects.create(
            user=self.user,
            project=self.project,
            subject=self.subject,
            character=AIJob.Character.SADA,
            task_type=AIJob.TaskType.SADA_TRANSCRIBE_AUDIO,
            source=self.audio,
            idempotency_key='sada-derived-source-test',
            status=AIJob.Status.PROCESSING,
        )
        self.transcript = 'بروتوكول زفير-913 يستخدم سبع مراحل تحقق.'
        self.result = {
            'full_transcript': self.transcript,
            'cleaned_transcript': self.transcript,
            'language': 'ar',
            'duration_seconds': 42,
        }

    def tearDown(self):
        cache.clear()
        self.media_override.disable()

    def _materialize(self, data=None):
        payload = dict(data if data is not None else self.result)
        return materialize_job(self.job, payload)

    def test_the_derived_source_is_a_complete_source_with_real_bytes(self):
        self._materialize()

        derived = StudentSource.objects.get(metadata__derived_from='sada')
        self.assertTrue(derived.file, 'the derived source has no file')
        with derived.file.open('rb') as handle:
            stored = handle.read()
        self.assertEqual(stored.decode('utf-8'), self.transcript)
        self.assertEqual(derived.file_size, len(self.transcript.encode('utf-8')))
        self.assertEqual(derived.mime_type, 'text/plain')
        self.assertEqual(derived.source_type, StudentSource.SourceType.TEXT)
        self.assertEqual(derived.status, StudentSource.Status.READY)

    def test_the_content_hash_resolves_through_the_canonical_mechanism(self):
        """This is the exact call that used to raise Http404 at dispatch."""
        self._materialize()
        derived = StudentSource.objects.get(metadata__derived_from='sada')

        checksum = content_sha256(derived)

        self.assertEqual(len(checksum), 64)
        self.assertEqual(
            checksum, hashlib.sha256(self.transcript.encode('utf-8')).hexdigest()
        )

    def test_the_derived_source_dispatches_without_raising(self):
        """The end of the loop: a downstream character job can be built."""
        self._materialize()
        derived = StudentSource.objects.get(metadata__derived_from='sada')

        downstream = AIJob.objects.create(
            user=self.user,
            project=self.project,
            subject=self.subject,
            character=AIJob.Character.KHOLASA,
            task_type=AIJob.TaskType.KHOLASA_GENERATE_SUMMARY,
            source=derived,
            idempotency_key='downstream-kholasa',
            status=AIJob.Status.QUEUED,
        )

        payload = build_service_payload(downstream)

        self.assertEqual(payload['source_ids'], [str(derived.id)])
        self.assertIn(str(derived.id), payload['source_versions'])
        self.assertEqual(len(payload['source_versions'][str(derived.id)]), 64)

    def test_ownership_project_and_folder_are_preserved(self):
        self._materialize()
        derived = StudentSource.objects.get(metadata__derived_from='sada')

        self.assertEqual(derived.user_id, self.user.id)
        self.assertEqual(derived.project_id, self.project.id)
        self.assertEqual(derived.subject_id, self.subject.id)
        # Lands beside the recording it came from, not loose in the library.
        self.assertEqual(derived.collection_id, self.collection.id)

    def test_a_replayed_callback_does_not_create_a_second_transcript(self):
        """A duplicate callback must not leave the learner two copies.

        Exercised through complete_job, which is how a replay actually
        arrives. Three guards stand in the way and this pins all of them:
        complete_job returns early for an already-COMPLETED job, Transcription
        has a unique constraint on ai_job, and the derived source is looked up
        by job before being created.
        """
        self.job.status = AIJob.Status.PROCESSING
        self.job.save(update_fields=['status'])

        complete_job(self.job, dict(self.result))
        complete_job(self.job, dict(self.result))

        self.assertEqual(
            StudentSource.objects.filter(metadata__derived_from='sada').count(), 1
        )
        self.assertEqual(Transcription.objects.filter(ai_job=self.job).count(), 1)

    def test_the_derived_source_lookup_is_itself_idempotent(self):
        """The guard inside the materializer, independent of the outer ones:
        a second attempt resolves to the existing row rather than adding one."""
        self._materialize()
        transcription = Transcription.objects.get(ai_job=self.job)

        first = StudentSource.objects.get(metadata__derived_from='sada')
        again = _create_derived_text_source(self.job, transcription, self.transcript)

        self.assertEqual(again, str(first.id))
        self.assertEqual(
            StudentSource.objects.filter(metadata__derived_from='sada').count(), 1
        )

    def test_an_empty_transcript_creates_no_source(self):
        with self.assertRaises(ValidationError):
            self._materialize({**self.result, 'full_transcript': '   '})
        self.assertFalse(
            StudentSource.objects.filter(metadata__derived_from='sada').exists()
        )

    def test_the_derived_source_is_marked_as_a_generated_artifact(self):
        """Storage accounting is unchanged -- the aggregate still counts this
        row -- but the flag is what a later billing policy would filter on."""
        self._materialize()
        derived = StudentSource.objects.get(metadata__derived_from='sada')

        self.assertIs(derived.metadata['generated_artifact'], True)
        self.assertEqual(derived.metadata['ai_job_id'], str(self.job.public_id))

    def test_the_derived_source_is_offered_to_the_text_characters(self):
        self._materialize()
        derived = StudentSource.objects.get(metadata__derived_from='sada')

        capabilities = get_source_character_capabilities(derived)

        self.assertTrue(capabilities['kholasa']['available'])
        self.assertTrue(capabilities['fahes']['available'])
        self.assertFalse(capabilities['sada']['available'])


class JobSourceScopeTests(APITestCase):
    """A job's source scope is decided once, at acceptance, and recorded.

    Dispatch used to re-derive it from `job.collection`, so moving a source
    between folders after the job was accepted silently changed which material
    it ran on -- and selecting more than ten sources was quietly sliced to ten
    with no indication that the rest were ignored.
    """

    def setUp(self):
        self.media_override = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        self.media_override.enable()
        self.user = User.objects.create_user(
            email='scope@example.com', password='StrongPass123!', full_name='Scope User'
        )
        stage = EducationStage.objects.create(name='Secondary', order=1)
        self.subject = Subject.objects.create(
            name='Chemistry', education_stage=stage, grade_level='12'
        )
        self.project = Project.objects.create(owner=self.user, title='Chemistry Project')
        self.collection = StudentSourceCollection.objects.create(
            user=self.user, project=self.project, subject=self.subject, name='Unit 1'
        )
        ensure_default_plans()
        get_or_create_user_subscription(self.user)

    def tearDown(self):
        cache.clear()
        self.media_override.disable()

    def _source(self, name, *, collection=None):
        body = f'Content of {name}'.encode()
        return StudentSource.objects.create(
            user=self.user,
            project=self.project,
            subject=self.subject,
            collection=collection,
            title=name,
            source_type=StudentSource.SourceType.TEXT,
            file=SimpleUploadedFile(f'{name}.txt', body, content_type='text/plain'),
            original_filename=f'{name}.txt',
            file_size=len(body),
            mime_type='text/plain',
            extension='txt',
            extracted_text=f'Content of {name}',
            status=StudentSource.Status.READY,
        )

    def test_the_selected_set_is_recorded_on_the_job(self):
        first = self._source('alpha', collection=self.collection)
        second = self._source('beta', collection=self.collection)

        job, _ = create_ai_job(
            user=self.user,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            collection=self.collection,
        )

        self.assertEqual(
            job.input_payload['source_ids'], [str(first.id), str(second.id)]
        )
        self.assertEqual(
            set(job.input_payload['source_versions']), {str(first.id), str(second.id)}
        )
        self.assertTrue(
            all(len(value) == 64 for value in job.input_payload['source_versions'].values())
        )

    def test_source_content_version_is_pinned_when_the_job_is_created(self):
        source = self._source('versioned', collection=self.collection)
        job, _ = create_ai_job(
            user=self.user,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            collection=self.collection,
        )
        accepted_sha = job.input_payload['source_versions'][str(source.id)]

        source.file.save(
            'versioned.txt',
            SimpleUploadedFile('versioned.txt', b'new source bytes', content_type='text/plain'),
            save=False,
        )
        source.file_size = len(b'new source bytes')
        source.metadata = {}
        source.save(update_fields=['file', 'file_size', 'metadata', 'updated_at'])

        with self.assertRaises(ValidationError) as caught:
            build_service_payload(job)

        self.assertEqual(caught.exception.domain_code, ErrorCode.SOURCE_VERSION_CHANGED)
        self.assertEqual(job.input_payload['source_versions'][str(source.id)], accepted_sha)

    @patch('apps.ai_integration.tasks.submit_job_to_service')
    def test_permanent_dispatch_failure_is_not_left_for_reconciliation(self, submit):
        source = self._source('permanent', collection=self.collection)
        job, _ = create_ai_job(
            user=self.user,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            source=source,
        )
        submit.side_effect = AIRequestError(
            {'source': 'changed'}, code=ErrorCode.SOURCE_VERSION_CHANGED
        )

        result = dispatch_ai_job.run(job.pk)

        self.assertEqual(result, str(job.public_id))
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.FAILED)
        self.assertEqual(job.error_code, ErrorCode.SOURCE_VERSION_CHANGED)
        outbox = AIJobDispatchOutbox.objects.get(job=job)
        self.assertEqual(outbox.status, AIJobDispatchOutbox.Status.FAILED)

    def test_moving_a_source_out_of_the_folder_does_not_change_an_accepted_job(self):
        """The defect this guards: dispatch re-derived scope from the folder,
        so a library tidy-up rewrote what an in-flight job meant."""
        first = self._source('alpha', collection=self.collection)
        second = self._source('beta', collection=self.collection)
        job, _ = create_ai_job(
            user=self.user,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            collection=self.collection,
        )

        # The learner reorganises their library after submitting.
        second.collection = None
        second.save(update_fields=['collection'])

        payload = build_service_payload(job)

        self.assertEqual(payload['source_ids'], [str(first.id), str(second.id)])
        self.assertEqual(len(payload['source_versions']), 2)

    def test_adding_a_source_to_the_folder_does_not_widen_an_accepted_job(self):
        self._source('alpha', collection=self.collection)
        job, _ = create_ai_job(
            user=self.user,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            collection=self.collection,
        )

        self._source('added-later', collection=self.collection)

        self.assertEqual(len(build_service_payload(job)['source_ids']), 1)

    def test_a_deleted_source_fails_loudly_rather_than_shrinking_the_job(self):
        first = self._source('alpha', collection=self.collection)
        self._source('beta', collection=self.collection)
        job, _ = create_ai_job(
            user=self.user,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            collection=self.collection,
        )
        first.delete()

        with self.assertRaises(ValidationError) as caught:
            build_service_payload(job)

        self.assertEqual(caught.exception.domain_code, 'source_no_longer_available')

    def test_selecting_more_than_the_limit_is_refused_explicitly(self):
        """No silent slice: the learner must be told the request was refused."""
        for index in range(MAX_SOURCES_PER_JOB + 1):
            self._source(f'source-{index:02d}', collection=self.collection)

        with self.assertRaises(ValidationError) as caught:
            create_ai_job(
                user=self.user,
                task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
                collection=self.collection,
            )

        self.assertEqual(caught.exception.domain_code, 'too_many_sources')
        self.assertEqual(
            int(caught.exception.detail['limit']), MAX_SOURCES_PER_JOB
        )
        self.assertEqual(
            int(caught.exception.detail['selected']), MAX_SOURCES_PER_JOB + 1
        )
        self.assertFalse(AIJob.objects.exists(), 'a refused request must create no job')

    def test_exactly_the_limit_is_accepted(self):
        for index in range(MAX_SOURCES_PER_JOB):
            self._source(f'source-{index:02d}', collection=self.collection)

        job, _ = create_ai_job(
            user=self.user,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            collection=self.collection,
        )

        self.assertEqual(len(job.input_payload['source_ids']), MAX_SOURCES_PER_JOB)

    def test_a_recorded_id_cannot_widen_access_beyond_the_owner(self):
        """Defence in depth: even a tampered payload stays inside the owner."""
        intruder = User.objects.create_user(
            email='intruder@example.com', password='StrongPass123!', full_name='Intruder'
        )
        victim_project = Project.objects.create(owner=intruder, title='Private')
        victim_source = StudentSource.objects.create(
            user=intruder,
            project=victim_project,
            title='Private notes',
            source_type=StudentSource.SourceType.TEXT,
            file=SimpleUploadedFile('p.txt', b'secret', content_type='text/plain'),
            original_filename='p.txt',
            file_size=6,
            mime_type='text/plain',
            extension='txt',
            status=StudentSource.Status.READY,
        )
        mine = self._source('mine', collection=self.collection)
        job, _ = create_ai_job(
            user=self.user,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            collection=self.collection,
        )
        job.input_payload = {
            **job.input_payload,
            'source_ids': [str(mine.id), str(victim_source.id)],
        }
        job.save(update_fields=['input_payload'])

        with self.assertRaises(ValidationError) as caught:
            build_service_payload(job)

        # Filtered out by the owner scope, then reported as missing -- never
        # silently included, and never leaked.
        self.assertEqual(caught.exception.domain_code, 'source_no_longer_available')

    def test_a_single_source_job_still_resolves_without_a_recorded_list(self):
        """Sada records a singular `source_id`, and jobs predating the scope
        record have no list at all. Their FK is already immutable."""
        source = self._source('solo')
        job = AIJob.objects.create(
            user=self.user,
            project=self.project,
            subject=self.subject,
            character=AIJob.Character.FAHES,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            source=source,
            idempotency_key='legacy-no-source-ids',
            status=AIJob.Status.QUEUED,
            input_payload={'question_count': 10},
        )

        self.assertEqual(resolve_job_sources(job), [source])


class ExplicitMultiSourceScopeTests(APITestCase):
    """Selecting several sources for one request must not reorganise the
    learner's library.

    The web client used to express "these three sources" by bulk-reassigning
    them into a collection -- a permanent, user-visible change to their
    folders made only to describe one temporary request.
    """

    def setUp(self):
        self.media_override = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        self.media_override.enable()
        self.user = User.objects.create_user(
            email='multi@example.com', password='StrongPass123!', full_name='Multi User'
        )
        self.other = User.objects.create_user(
            email='multi-other@example.com', password='StrongPass123!', full_name='Other'
        )
        stage = EducationStage.objects.create(name='Secondary', order=1)
        self.subject = Subject.objects.create(
            name='Biology', education_stage=stage, grade_level='12'
        )
        self.project = Project.objects.create(owner=self.user, title='Biology Project')
        ensure_default_plans()
        get_or_create_user_subscription(self.user)
        self.first = self._source('alpha')
        self.second = self._source('beta')

    def tearDown(self):
        cache.clear()
        self.media_override.disable()

    def _source(self, name, *, owner=None, project=None):
        owner = owner or self.user
        body = f'Content of {name}'.encode()
        return StudentSource.objects.create(
            user=owner,
            project=project or self.project,
            subject=self.subject,
            title=name,
            source_type=StudentSource.SourceType.TEXT,
            file=SimpleUploadedFile(f'{name}.txt', body, content_type='text/plain'),
            original_filename=f'{name}.txt',
            file_size=len(body),
            mime_type='text/plain',
            extension='txt',
            extracted_text=f'Content of {name}',
            status=StudentSource.Status.READY,
        )

    def _create(self, payload):
        self.client.force_authenticate(self.user)
        return self.client.post(reverse('ai-job-list'), payload, format='json')

    def test_an_explicit_selection_creates_a_job_without_touching_collections(self):
        before = StudentSourceCollection.objects.count()

        response = self._create({
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source_ids': [self.first.id, self.second.id],
            'project': str(self.project.public_id),
        })

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)
        self.assertEqual(StudentSourceCollection.objects.count(), before)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertIsNone(self.first.collection_id)
        self.assertIsNone(self.second.collection_id)

    def test_the_exact_selection_is_recorded_and_replayed_at_dispatch(self):
        response = self._create({
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source_ids': [self.first.id, self.second.id],
            'project': str(self.project.public_id),
        })

        job = AIJob.objects.get(public_id=response.data['public_id'])
        self.assertEqual(
            job.input_payload['source_ids'], [str(self.first.id), str(self.second.id)]
        )
        payload = build_service_payload(job)
        self.assertEqual(payload['source_ids'], [str(self.first.id), str(self.second.id)])
        self.assertEqual(len(payload['source_versions']), 2)

    def test_every_selected_source_is_authorized_not_just_the_first(self):
        """The dangerous shape: a legitimate first id followed by someone
        else's."""
        stolen = self._source('victim', owner=self.other,
                              project=Project.objects.create(owner=self.other, title='Theirs'))

        response = self._create({
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source_ids': [self.first.id, stolen.id],
            'project': str(self.project.public_id),
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(AIJob.objects.exists())

    def test_sources_from_two_projects_are_refused(self):
        elsewhere = Project.objects.create(owner=self.user, title='Another project')
        other_project_source = self._source('gamma', project=elsewhere)

        response = self._create({
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source_ids': [self.first.id, other_project_source.id],
            'project': str(self.project.public_id),
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_selection_beyond_the_limit_is_refused_with_its_own_code(self):
        selected = [self._source(f'extra-{i:02d}').id for i in range(MAX_SOURCES_PER_JOB + 1)]

        response = self._create({
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source_ids': selected,
            'project': str(self.project.public_id),
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'too_many_sources')
        self.assertFalse(AIJob.objects.exists())

    def test_a_failed_source_in_the_selection_is_refused(self):
        broken = self._source('broken')
        StudentSource.objects.filter(pk=broken.pk).update(
            status=StudentSource.Status.FAILED
        )

        response = self._create({
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source_ids': [self.first.id, broken.id],
            'project': str(self.project.public_id),
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'source_not_ready')

    def test_mixing_an_explicit_selection_with_a_single_source_is_refused(self):
        response = self._create({
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source_ids': [self.first.id],
            'source': self.second.id,
            'project': str(self.project.public_id),
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_two_different_selections_are_two_different_jobs(self):
        """Idempotency must key on the selection, or the second request would
        return the first request's quiz."""
        third = self._source('gamma')

        first = self._create({
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source_ids': [self.first.id, self.second.id],
            'project': str(self.project.public_id),
        })
        second = self._create({
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source_ids': [self.first.id, third.id],
            'project': str(self.project.public_id),
        })

        self.assertEqual(second.status_code, status.HTTP_202_ACCEPTED, second.data)
        self.assertNotEqual(first.data['public_id'], second.data['public_id'])

    def test_repeating_the_same_selection_reuses_the_same_job(self):
        payload = {
            'task_type': AIJob.TaskType.FAHES_GENERATE_QUIZ,
            'source_ids': [self.first.id, self.second.id],
            'project': str(self.project.public_id),
        }

        first = self._create(payload)
        second = self._create(payload)

        self.assertEqual(first.data['public_id'], second.data['public_id'])
        self.assertEqual(AIJob.objects.count(), 1)


class JobProgressContractTests(APITestCase):
    """Progress the learner sees must come from the job, not the client.

    The web app rendered a hardcoded percentage per Django status. Django in
    turn matched incoming progress on its *own* status names, of which the AI
    service emits exactly one -- so a job sat at `submitted` for its entire
    life and the bar was pure decoration.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='progress@example.com', password='StrongPass123!', full_name='Progress'
        )
        stage = EducationStage.objects.create(name='Secondary', order=1)
        self.subject = Subject.objects.create(
            name='Maths', education_stage=stage, grade_level='12'
        )
        self.project = Project.objects.create(owner=self.user, title='Maths Project')
        self.job = AIJob.objects.create(
            user=self.user,
            project=self.project,
            subject=self.subject,
            character=AIJob.Character.FAHES,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            idempotency_key='progress-contract',
            status=AIJob.Status.SUBMITTED,
            external_job_id='ext-progress-1',
        )

    def tearDown(self):
        cache.clear()

    def test_every_ai_stage_maps_to_a_public_stage(self):
        """The AI service's full internal vocabulary, so a pipeline stage can
        never arrive as 'unrecognized' and be dropped."""
        ai_internal = {
            'queued', 'preparing', 'retrieving', 'planning', 'generating',
            'validating', 'repairing', 'materializing',
        }
        self.assertTrue(
            ai_internal <= set(AI_STAGE_MAP),
            f'unmapped AI stages: {sorted(ai_internal - set(AI_STAGE_MAP))}',
        )

    def test_progress_advances_through_the_real_pipeline_stages(self):
        seen = []
        for remote in ('preparing', 'retrieving', 'generating', 'validating'):
            self.job = record_ai_stage(self.job, remote)
            seen.append(public_progress_stage(self.job))

        self.assertEqual(seen, ['preparing', 'retrieving', 'generating', 'validating'])

    def test_progress_never_moves_backwards(self):
        """A slow or duplicated poll must not walk the learner back."""
        self.job = record_ai_stage(self.job, 'generating')
        self.assertEqual(public_progress_stage(self.job), 'generating')

        self.job = record_ai_stage(self.job, 'retrieving')

        self.assertEqual(public_progress_stage(self.job), 'generating')

    def test_an_unknown_stage_is_ignored_rather_than_applied(self):
        self.job = record_ai_stage(self.job, 'generating')
        self.job = record_ai_stage(self.job, 'some_future_stage')
        self.assertEqual(public_progress_stage(self.job), 'generating')

    def test_a_stale_stage_cannot_resurrect_a_completed_job(self):
        # result_type/result_id are required by the
        # ai_job_completed_has_materialized_result constraint -- a completed
        # job without a materialized result cannot exist.
        self.job.status = AIJob.Status.COMPLETED
        self.job.result_type = 'quiz'
        self.job.result_id = '1'
        self.job.save(update_fields=['status', 'result_type', 'result_id'])

        self.job = record_ai_stage(self.job, 'generating')

        self.assertEqual(public_progress_stage(self.job), 'completed')
        self.assertEqual(self.job.status, AIJob.Status.COMPLETED)

    def test_a_failed_job_reports_the_failed_stage(self):
        self.job.status = AIJob.Status.FAILED
        self.job.save(update_fields=['status'])
        self.assertEqual(public_progress_stage(self.job), 'failed')

    def test_the_api_exposes_the_stage_and_no_invented_percentage(self):
        self.job = record_ai_stage(self.job, 'retrieving')
        self.client.force_authenticate(self.user)

        response = self.client.get(reverse('ai-job-detail', args=[str(self.job.public_id)]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['progress_stage'], 'retrieving')
        # The AI service reports progress_percent as a constant 0 because its
        # own percentages were synthetic. Publishing one here would invent it
        # a second time.
        self.assertNotIn('progress_percent', response.data)

    def test_a_job_the_ai_has_not_reported_on_still_has_a_stage(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse('ai-job-detail', args=[str(self.job.public_id)]))
        self.assertEqual(response.data['progress_stage'], 'queued')
