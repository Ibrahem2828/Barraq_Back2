import json
import tempfile
import time
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.projects.models import Project
from apps.quizzes.models import Quiz
from apps.sources.models import StudentSource
from apps.subjects.models import EducationStage, Subject
from apps.subscriptions.models import UsageLedgerEntry
from apps.subscriptions.services import (
    ensure_default_plans,
    get_or_create_user_subscription,
    reserve_character_request,
)

from .client import AIServiceClient, AIServiceError
from .error_codes import ErrorCode
from .models import AIJob, AIWebhookEvent
from .security import make_service_signature
from .services import build_khota_job_input, build_service_payload, complete_job, update_job_progress

User = get_user_model()


@override_settings(AI_SERVICE_ENABLED=True, AI_SERVICE_BASE_URL='https://ai.example.test')
class AIServiceClientSafetyTests(SimpleTestCase):
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
