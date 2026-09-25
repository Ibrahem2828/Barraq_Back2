import json

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.projects.models import Project
from apps.sources.capabilities import PROJECT_REQUIRED_MESSAGE
from apps.sources.models import StudentSource, StudentSourceCollection
from apps.subjects.models import Subject

from .models import AIFeedback, AIJob


class CanonicalTaskTypeField(serializers.ChoiceField):
    """Accept legacy client values only long enough to persist a V2 value."""

    legacy_aliases = {
        "kholasa_summary": AIJob.TaskType.KHOLASA_GENERATE_SUMMARY,
        "sada_transcription": AIJob.TaskType.SADA_TRANSCRIBE_AUDIO,
        "kholasa_summarize": AIJob.TaskType.KHOLASA_GENERATE_SUMMARY,
        "sada_transcribe": AIJob.TaskType.SADA_TRANSCRIBE_AUDIO,
        "rasheed_recommend": AIJob.TaskType.RASHEED_RECOMMENDATIONS,
    }

    def to_internal_value(self, data):
        return super().to_internal_value(self.legacy_aliases.get(data, data))


class AIJobCreateSerializer(serializers.Serializer):
    task_type = CanonicalTaskTypeField(choices=AIJob.TaskType.choices)
    # Accepts the Project's public_id (not its internal numeric pk) — matches
    # the read side (AIJobListSerializer/AIJobSerializer.project below) and the
    # ?project=<public_id> filters already used on sources/quizzes/study-plans.
    project = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=Project.objects.filter(is_deleted=False, status=Project.Status.ACTIVE),
        required=False,
        allow_null=True,
    )
    # Every DRF Serializer is itself a Field, and Field.source is a real
    # (differently-typed) base attribute -- the DRF metaclass intercepts
    # declared fields before that collision ever matters at runtime, but
    # mypy still sees it as an incompatible override of the base attribute.
    source = serializers.PrimaryKeyRelatedField(  # type: ignore[assignment]
        queryset=StudentSource.objects.all(), required=False, allow_null=True
    )
    collection = serializers.PrimaryKeyRelatedField(queryset=StudentSourceCollection.objects.all(), required=False, allow_null=True)
    # An ephemeral multi-source scope. Before this existed, the web client
    # expressed "these three sources" by bulk-reassigning them into a
    # collection -- permanently reorganising the learner's library to describe
    # one request. Every id is authorized server-side; ownership is never
    # inferred from the fact that the browser sent it.
    source_ids = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(queryset=StudentSource.objects.all()),
        required=False,
        allow_empty=False,
    )
    subject = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.filter(is_active=True), required=False, allow_null=True)
    input = serializers.JSONField(required=False, default=dict)
    parameters = serializers.JSONField(required=False, default=dict)
    force = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        user = self.context['request'].user
        source = attrs.get('source')
        collection = attrs.get('collection')
        project = attrs.get('project')
        if project and project.owner_id != user.id:
            raise serializers.ValidationError({'project': 'You do not own this project.'})
        if source and source.user_id != user.id:
            raise serializers.ValidationError({'source': 'You do not own this source.'})
        if collection and collection.user_id != user.id:
            raise serializers.ValidationError({'collection': 'You do not own this collection.'})
        if source and collection:
            raise serializers.ValidationError('Choose either source or collection.')
        inherited_project = getattr(source, 'project', None) or getattr(collection, 'project', None)
        selected = attrs.get('source_ids') or []
        if selected and (source or collection):
            raise serializers.ValidationError(
                {'source_ids': 'Choose either explicit sources, one source, or one collection.'}
            )
        if selected:
            # Authorized in full by services.validate_selected_sources; this
            # early check keeps the project inference below honest.
            for item in selected:
                if item.user_id != user.id:
                    raise serializers.ValidationError(
                        {'source_ids': 'You do not own one of the selected sources.'}
                    )
            inherited_project = inherited_project or selected[0].project
        if inherited_project:
            if project and project.id != inherited_project.id:
                raise serializers.ValidationError({'project': 'Project must match the selected source or collection.'})
            attrs['project'] = inherited_project
        elif not project:
            # Blueprint 01_BACKEND.md §3.1: no study experience may run
            # without a project -- an explicit project is required whenever
            # one can't be inherited from the chosen source/collection.
            raise serializers.ValidationError({'project': PROJECT_REQUIRED_MESSAGE})
        task_type = attrs['task_type']
        if task_type in {
            AIJob.TaskType.FAHES_GENERATE_QUIZ,
            AIJob.TaskType.KHOLASA_GENERATE_SUMMARY,
        } and not (source or collection or selected):
            raise serializers.ValidationError('This task requires a source or collection.')
        if task_type == AIJob.TaskType.SADA_TRANSCRIBE_AUDIO:
            # The upstream transcription API accepts one audio asset per job.
            # A collection can contain non-audio files or several recordings,
            # neither of which has an unambiguous, safe transcription result.
            if collection or not source:
                raise serializers.ValidationError({'source': 'Sada requires exactly one audio source; collections are not supported.'})
            if source.source_type != source.SourceType.AUDIO:
                raise serializers.ValidationError({'source': 'Sada requires an audio source.'})
        if source and source.status in {source.Status.PROCESSING, source.Status.FAILED}:
            raise serializers.ValidationError({'source': 'The source is not ready for an AI request.'})
        encoded_size = len(json.dumps({'input': attrs.get('input', {}), 'parameters': attrs.get('parameters', {})}, ensure_ascii=False, default=str).encode('utf-8'))
        if encoded_size > 32 * 1024:
            raise serializers.ValidationError('AI request input and parameters must not exceed 32 KB.')
        return attrs


class AIJobListSerializer(serializers.ModelSerializer):
    # See AIJobCreateSerializer.project — exposed as public_id, not the pk.
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = AIJob
        fields = (
            'public_id', 'character', 'task_type', 'status', 'source', 'collection', 'subject',
            'project', 'contract_version',
            'result_type', 'result_id', 'error_code', 'submitted_at', 'completed_at',
            'created_at', 'updated_at',
        )
        read_only_fields = fields


class AIJobSerializer(serializers.ModelSerializer):
    # See AIJobCreateSerializer.project — exposed as public_id, not the pk.
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)
    # The public progress vocabulary. Deliberately not the AI service's
    # internal enum, and deliberately not a percentage: the AI service reports
    # progress_percent as a constant 0 because its own percentages were
    # synthetic, so a number here would be invented twice over.
    progress_stage = serializers.SerializerMethodField()

    class Meta:
        model = AIJob
        fields = (
            'public_id', 'character', 'task_type', 'status', 'progress_stage',
            'source', 'collection', 'subject',
            'project', 'contract_version', 'request_id',
            'external_job_id', 'input_payload', 'parameters', 'result_payload', 'result_type',
            'result_id', 'error_code', 'error_message', 'credits_reserved', 'credits_committed',
            'quality_metrics', 'security_flags', 'output_schema_version',
            'provider_account', 'model_name', 'prompt_version',
            'input_tokens', 'output_tokens', 'cost_usd',
            'submitted_at', 'completed_at', 'created_at', 'updated_at',
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_progress_stage(self, obj):
        from .services import public_progress_stage

        return public_progress_stage(obj)


class AIFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIFeedback
        fields = (
            'id', 'job', 'rating', 'is_helpful', 'feedback_type', 'reason_codes', 'comment',
            'corrected_output', 'training_consent', 'consent_version', 'forwarded_to_ai_service',
            'provider_snapshot', 'model_snapshot', 'prompt_version_snapshot',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'job', 'forwarded_to_ai_service',
            'provider_snapshot', 'model_snapshot', 'prompt_version_snapshot',
            'created_at', 'updated_at',
        )

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError('Rating must be between 1 and 5.')
        return value
