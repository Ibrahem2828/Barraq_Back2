from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import filters, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.quizzes.models import AttemptStatusChoices, QuizAttempt
from apps.sources.models import StudentSource, StudentSourceCollection
from apps.students.models import StudentProfile
from apps.study_plans.models import StudyTask
from apps.subjects.models import UserSubject

from .client import AIServiceClient, AIServiceError
from .error_codes import ErrorCode
from .models import AIFeedback, AIJob, AIWebhookEvent
from .security import HasInternalServiceKey, verify_webhook
from .serializers import AIFeedbackSerializer, AIJobCreateSerializer, AIJobListSerializer, AIJobSerializer
from .services import cancel_job, complete_job, content_sha256, create_ai_job, fail_job, update_job_progress
from .tasks import forward_ai_feedback


def _assert_requested_owner(request, resource):
    """Bind every internal source read to the authenticated job user."""

    raw_user_id = request.query_params.get("user_id")
    if raw_user_id in (None, ""):
        raise ValidationError({"user_id": "A positive integer is required."})
    try:
        requested_user_id = int(raw_user_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"user_id": "A positive integer is required."}) from exc
    if requested_user_id <= 0:
        raise ValidationError({"user_id": "A positive integer is required."})
    if resource.user_id != requested_user_id:
        raise Http404


def _remote_failure_error(payload):
    """Build an ``AIServiceError`` carrying the AI service's own error code
    (if it named one) so ``fail_job`` records a real taxonomy code on
    ``AIJob.error_code`` instead of the generic ``ai_job_failed`` fallback."""

    remote_code = str(payload.get('error_code') or (payload.get('error') or {}).get('code') or '')
    code = remote_code if remote_code in ErrorCode.values_set() else ErrorCode.PROVIDER_UNAVAILABLE
    return AIServiceError(
        payload.get('error_message') or 'AI service job failed.', code=code, retryable=False
    )


@extend_schema(tags=['AI Jobs'])
class AIJobViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'public_id'
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['created_at', 'completed_at']
    ordering = ['-created_at']

    def get_throttles(self):
        self.throttle_scope = 'ai_requests' if self.action == 'create' else None
        return super().get_throttles()

    def get_queryset(self):
        return AIJob.objects.filter(user=self.request.user).select_related('project', 'source', 'collection', 'subject')

    def get_serializer_class(self):
        if self.action == 'create':
            return AIJobCreateSerializer
        if self.action == 'list':
            return AIJobListSerializer
        return AIJobSerializer

    def list(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        for field in ('status', 'character', 'task_type'):
            value = request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        project = request.query_params.get('project')
        if project:
            queryset = queryset.filter(project__public_id=project)
        page = self.paginate_queryset(queryset)
        serializer = AIJobListSerializer(page or queryset, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    def create(self, request):
        serializer = AIJobCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        job, created = create_ai_job(
            user=request.user,
            task_type=data['task_type'],
            project=data.get('project'),
            source=data.get('source'),
            collection=data.get('collection'),
            sources=data.get('source_ids'),
            subject=data.get('subject'),
            input_payload=data.get('input'),
            parameters=data.get('parameters'),
            force=data.get('force', False),
        )
        return Response(AIJobSerializer(job).data, status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK)

    def retrieve(self, request, public_id=None):
        return Response(AIJobSerializer(self.get_object()).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, public_id=None):
        return Response(AIJobSerializer(cancel_job(self.get_object())).data)

    @action(detail=True, methods=['post'])
    def refresh(self, request, public_id=None):
        job = self.get_object()
        if not job.external_job_id or job.status in {AIJob.Status.COMPLETED, AIJob.Status.FAILED, AIJob.Status.CANCELED}:
            return Response(AIJobSerializer(job).data)
        try:
            data = AIServiceClient().get_job(
                job.external_job_id, user_id=job.user_id
            ).data
        except AIServiceError as exc:
            return Response({'success': False, 'message': str(exc), 'code': exc.code}, status=exc.status_code or 503)
        remote_status = str(data.get('status') or '').lower()
        # The polled JobView nests everything the completed job produced
        # under "output" (result, quality/groundedness scores, provider,
        # usage, security_flags) -- there is no top-level "result" or
        # "quality_metrics" key on this response, unlike the webhook payload.
        output = data.get('output') or {}
        if remote_status == AIJob.Status.COMPLETED and output.get('result'):
            job = complete_job(job, output['result'], {
                'refresh_response': data,
                'quality_metrics': {
                    'quality_score': output.get('quality_score'),
                    'groundedness_score': output.get('groundedness_score'),
                },
                'security_flags': output.get('security_flags') or [],
                'usage': {
                    'input_tokens': output.get('input_tokens'),
                    'output_tokens': output.get('output_tokens'),
                    'estimated_cost_usd': output.get('estimated_cost_usd'),
                },
                'provider': {
                    'account': output.get('provider_account'),
                    'model': output.get('model_name'),
                },
            })
        elif remote_status == AIJob.Status.FAILED:
            job = fail_job(job, _remote_failure_error(data))
        elif remote_status == AIJob.Status.CANCELED:
            job = cancel_job(job)
        elif remote_status in {AIJob.Status.SUBMITTED, AIJob.Status.PROCESSING, AIJob.Status.VALIDATING}:
            job = update_job_progress(job, remote_status, metadata={'last_refresh': data})
        return Response(AIJobSerializer(job).data)

    @action(detail=True, methods=['post'], url_path='feedback')
    def feedback(self, request, public_id=None):
        job = self.get_object()
        if job.status != AIJob.Status.COMPLETED:
            return Response({'detail': 'Feedback is accepted only for completed AI jobs.'}, status=status.HTTP_409_CONFLICT)
        serializer = AIFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Provider/model/prompt_version are a snapshot of what actually
        # generated this result -- captured from the job (populated from the
        # completion webhook's metadata.provider/.prompt), never accepted
        # from the request body (spec: "لا يحددها المستخدم").
        feedback, created = AIFeedback.objects.update_or_create(
            job=job,
            user=request.user,
            defaults={
                **serializer.validated_data,
                'consent_version': serializer.validated_data.get('consent_version') or settings.AI_DATASET_CONSENT_VERSION,
                'provider_snapshot': job.provider_account[:40],
                'model_snapshot': job.model_name[:100],
                'prompt_version_snapshot': job.prompt_version[:40],
            },
        )
        transaction.on_commit(lambda: forward_ai_feedback.delay(feedback.pk))
        return Response(
            AIFeedbackSerializer(feedback).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(
    tags=['AI Jobs'],
    responses=inline_serializer(
        name='AICapabilities',
        fields={
            'service_enabled': serializers.BooleanField(),
            'characters': serializers.ListField(child=serializers.CharField()),
            'task_types': serializers.ListField(child=serializers.CharField()),
            'phase_one': serializers.ListField(child=serializers.CharField()),
            'phase_two': serializers.ListField(child=serializers.CharField()),
        },
    ),
)
class AICapabilitiesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response({
            'service_enabled': settings.AI_SERVICE_ENABLED,
            'characters': [value for value, _ in AIJob.Character.choices],
            'task_types': [value for value, _ in AIJob.TaskType.choices],
            'phase_one': ['fahes', 'khota', 'rasheed'],
            'phase_two': ['kholasa', 'sada'],
        })


@extend_schema(tags=['AI Jobs'], responses={200: OpenApiTypes.OBJECT, 503: OpenApiTypes.OBJECT})
class AIServiceHealthView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        try:
            data = AIServiceClient().health().data
            return Response({'status': 'ok', 'service': data})
        except AIServiceError as exc:
            return Response({'status': 'error', 'message': str(exc), 'code': exc.code}, status=503)


@extend_schema(
    tags=['AI Internal'],
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    description='Signed webhook callback from the standalone AI service reporting an AIJob result or status update.',
)
class AIWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        raw_body = request._request.body
        if not verify_webhook(request, body=raw_body):
            return Response({'detail': 'Invalid webhook signature.'}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            payload = json.loads(raw_body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Response({'detail': 'Invalid JSON payload.'}, status=status.HTTP_400_BAD_REQUEST)
        event_id = str(payload.get('event_id') or '')
        external_job_id = str(payload.get('job_id') or payload.get('external_job_id') or '')
        if not event_id or not external_job_id:
            return Response({'detail': 'event_id and job_id are required.'}, status=status.HTTP_400_BAD_REQUEST)
        payload_hash = hashlib.sha256(raw_body).hexdigest()
        event, created = AIWebhookEvent.objects.get_or_create(
            event_id=event_id,
            defaults={
                'event_type': str(payload.get('event_type') or 'job.updated'),
                'external_job_id': external_job_id,
                'payload_hash': payload_hash,
            },
        )
        if not created and event.payload_hash != payload_hash:
            # Same event_id, different content: a genuine conflict, not a
            # retry -- accepting it silently would risk applying whichever
            # delivery happened to win the race.
            return Response(
                {'detail': 'event_id was reused with a different payload.', 'code': ErrorCode.IDEMPOTENCY_CONFLICT},
                status=status.HTTP_409_CONFLICT,
            )
        if not created and event.processed:
            return Response({'accepted': True, 'duplicate': True})
        job = AIJob.objects.filter(external_job_id=external_job_id).first()
        if job is None:
            event.error_message = 'Unknown external job.'
            event.save(update_fields=['error_message'])
            return Response({'detail': 'Unknown job.'}, status=status.HTTP_404_NOT_FOUND)
        remote_status = str(payload.get('status') or '').lower()
        recognized_statuses = {
            AIJob.Status.COMPLETED,
            AIJob.Status.FAILED,
            AIJob.Status.CANCELED,
            AIJob.Status.SUBMITTED,
            AIJob.Status.PROCESSING,
            AIJob.Status.VALIDATING,
        }
        if remote_status not in recognized_statuses:
            event.error_message = f'Unrecognized status "{remote_status}".'
            event.save(update_fields=['error_message'])
            return Response(
                {'detail': 'Unrecognized status.', 'code': ErrorCode.INVALID_CONTRACT},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            if remote_status == AIJob.Status.COMPLETED:
                # The AI service nests everything under "metadata"
                # (metadata.usage, metadata.quality, metadata.provider,
                # metadata.security_flags) -- see
                # Baraq_AI/app/services/webhook_delivery.py:build_result_webhook_payload.
                # There is no top-level "telemetry"/"quality_metrics" key.
                webhook_metadata = payload.get('metadata') or {}
                complete_job(job, payload.get('result') or {}, {
                    'webhook': webhook_metadata,
                    'quality_metrics': webhook_metadata.get('quality') or {},
                    'security_flags': webhook_metadata.get('security_flags') or [],
                    'usage': webhook_metadata.get('usage') or {},
                    'provider': webhook_metadata.get('provider') or {},
                    'prompt': webhook_metadata.get('prompt') or {},
                })
            elif remote_status == AIJob.Status.FAILED:
                fail_job(job, _remote_failure_error(payload))
            elif remote_status == AIJob.Status.CANCELED:
                cancel_job(job)
            else:
                update_job_progress(job, remote_status, metadata={'webhook': payload.get('metadata') or {}})
            event.processed = True
            event.processed_at = timezone.now()
            event.save(update_fields=['processed', 'processed_at'])
        except Exception as exc:
            event.error_message = str(exc)[:2000]
            event.save(update_fields=['error_message'])
            raise
        return Response({'accepted': True})


@extend_schema(
    tags=['AI Internal'],
    responses=OpenApiTypes.OBJECT,
    description='Source metadata and a signed download URL, for the AI service to fetch.',
)
class InternalSourceManifestView(APIView):
    permission_classes = [HasInternalServiceKey]
    authentication_classes = []

    def get(self, request, pk):
        source = get_object_or_404(StudentSource.objects.select_related('subject', 'collection'), pk=pk)
        _assert_requested_owner(request, source)
        return Response({
            'source_id': str(source.id),
            'owner_user_id': str(source.user_id),
            'project_id': str(source.project_id) if source.project_id else None,
            'title': source.title,
            'mime_type': source.mime_type,
            'size_bytes': source.file_size,
            'content_sha256': content_sha256(source),
            'subject_id': str(source.subject_id) if source.subject_id else None,
            'metadata': {
                'source_type': source.source_type,
                'status': source.status,
                'extension': source.extension,
            },
        })


@extend_schema(
    tags=['AI Internal'],
    responses={200: OpenApiTypes.BINARY},
    description='Streams the raw source file to the AI service for processing.',
)
class InternalSourceDownloadView(APIView):
    permission_classes = [HasInternalServiceKey]
    authentication_classes = []

    def get(self, request, pk):
        source = get_object_or_404(StudentSource, pk=pk)
        _assert_requested_owner(request, source)
        if not source.file:
            raise Http404
        response = FileResponse(source.file.open('rb'), content_type=source.mime_type or 'application/octet-stream')
        extension = f'.{source.extension}' if source.extension else ''
        response['Content-Disposition'] = f'attachment; filename="source-{source.id}{extension}"'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


@extend_schema(
    tags=['AI Internal'],
    responses=OpenApiTypes.OBJECT,
    description='Collection metadata and its member sources, each with a manifest URL, for the AI service.',
)
class InternalCollectionManifestView(APIView):
    permission_classes = [HasInternalServiceKey]
    authentication_classes = []

    def get(self, request, pk):
        collection = get_object_or_404(StudentSourceCollection.objects.prefetch_related('sources'), pk=pk)
        _assert_requested_owner(request, collection)
        return Response({
            'collection_id': str(collection.id),
            'owner_user_id': str(collection.user_id),
            'project_id': str(collection.project_id) if collection.project_id else None,
            'title': collection.name,
            'source_ids': [
                str(source.id)
                for source in collection.sources.filter(
                    status__in=[StudentSource.Status.UPLOADED, StudentSource.Status.READY]
                ).order_by('id')
            ],
            'metadata': {
                'subject_id': str(collection.subject_id) if collection.subject_id else None,
                'status': collection.status,
            },
        })


@extend_schema(
    tags=['AI Internal'],
    responses=OpenApiTypes.OBJECT,
    description='Student profile, subjects, and performance summary, for the AI service to ground its responses.',
)
class InternalUserContextView(APIView):
    permission_classes = [HasInternalServiceKey]
    authentication_classes = []

    def get(self, request, pk):
        profile = StudentProfile.objects.select_related('education_stage').filter(user_id=pk).first()
        subjects = UserSubject.objects.filter(user_id=pk).select_related('subject')
        quiz_metrics = QuizAttempt.objects.filter(
            user_id=pk, status=AttemptStatusChoices.SUBMITTED
        ).aggregate(
            attempts=Count('id'),
            average_percentage=Avg('percentage'),
        )
        task_metrics = StudyTask.objects.filter(plan__user_id=pk).aggregate(
            total=Count('id'),
            completed=Count('id', filter=Q(status=StudyTask.Status.COMPLETED)),
            skipped=Count('id', filter=Q(status=StudyTask.Status.SKIPPED)),
        )
        return Response({
            'user_id': str(pk),
            'education_stage': getattr(getattr(profile, 'education_stage', None), 'name', None),
            'grade_level': getattr(profile, 'grade_level', '') or None,
            'specialization': getattr(profile, 'specialization', '') or None,
            'daily_study_minutes': (
                int(profile.daily_study_hours * 60)
                if profile and profile.daily_study_hours is not None
                else None
            ),
            'selected_subjects': [{'id': str(item.subject_id), 'name': item.subject.name} for item in subjects],
            'exam_dates': {},
            'authoritative_metrics': {
                'quiz_attempts': quiz_metrics['attempts'] or 0,
                'average_quiz_percentage': float(quiz_metrics['average_percentage'] or 0),
                'study_tasks_total': task_metrics['total'] or 0,
                'study_tasks_completed': task_metrics['completed'] or 0,
                'study_tasks_skipped': task_metrics['skipped'] or 0,
            },
        })


@extend_schema(tags=['AI Jobs'], responses=OpenApiTypes.OBJECT, description='Route index for the AI integration API.')
class AIApiRootView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        base = request.build_absolute_uri('/').rstrip('/')
        return Response({
            'jobs': f'{base}/api/v1/ai/jobs/',
            'capabilities': f'{base}/api/v1/ai/capabilities/',
            'service_health': f'{base}/api/v1/ai/service-health/',
        })
