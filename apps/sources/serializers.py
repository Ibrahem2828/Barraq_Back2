from pathlib import Path

from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.reverse import reverse

from apps.projects.models import Project
from apps.quizzes.serializers import QuizListSerializer
from apps.study_plans.serializers import StudyPlanListSerializer
from apps.subjects.models import Subject
from apps.subjects.serializers import SubjectSerializer
from apps.subscriptions.services import get_user_features

from .capabilities import (
    get_collection_character_capabilities,
    get_source_character_capabilities,
)
from .models import StudentSource, StudentSourceCollection, StudentSourceInteraction
from .validators import validate_student_source_file


# Resolves subscription features once per serialization.
#
# The four capability call sites below used to omit `features` entirely, so a
# list or detail response advertised Kholasa and Sada to a Free user while
# `/capabilities/` -- the endpoint written for exactly this question, and
# which does pass them -- said the opposite on the same page load.
#
# Cached on the serializer context so a list of N sources performs one
# subscription lookup rather than N.
#
# Deliberately a comment, not a docstring: drf-spectacular publishes the first
# base class's docstring as the schema description, and this note is
# implementation detail that does not belong in the client contract.
class _CapabilityFeaturesMixin:
    def _features(self):
        context = self.context
        if 'capability_features' not in context:
            request = context.get('request')
            user = getattr(request, 'user', None)
            context['capability_features'] = (
                get_user_features(user)
                if user is not None and getattr(user, 'is_authenticated', False)
                else None
            )
        return context['capability_features']


class StudentSourceInteractionSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentSourceInteraction
        fields = (
            'id',
            'source',
            'collection',
            'character',
            'action',
            'status',
            'result_type',
            'result_id',
            'message',
            'metadata',
            'created_at',
        )


class StudentSourceBriefSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    collection_name = serializers.CharField(source='collection.name', read_only=True)
    collection_id = serializers.IntegerField(source='collection.id', read_only=True)
    # Exposed as the Project's public_id (not its internal numeric pk) so a client
    # can round-trip this value straight into /projects/{public_id}/... routes,
    # matching the ?project=<public_id> filter this same list already accepts.
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = StudentSource
        fields = (
            'id',
            'title',
            'source_type',
            'project',
            'subject',
            'subject_name',
            'collection',
            'collection_id',
            'collection_name',
            'original_filename',
            'file_size',
            'extension',
            'status',
            'created_at',
        )


class StudentSourceCollectionListSerializer(serializers.ModelSerializer):
    subject = SubjectSerializer(read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    source_count = serializers.IntegerField(read_only=True)
    total_file_size = serializers.IntegerField(read_only=True)
    last_source_at = serializers.DateTimeField(read_only=True, allow_null=True)
    # See StudentSourceBriefSerializer.project — exposed as public_id, not the pk.
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = StudentSourceCollection
        # tuple[str, ...] so StudentSourceCollectionDetailSerializer.Meta can
        # extend this tuple without mypy flagging a fixed-length override.
        fields: tuple[str, ...] = (
            'id',
            'name',
            'project',
            'description',
            'subject',
            'subject_name',
            'color',
            'icon',
            'status',
            'source_count',
            'total_file_size',
            'last_source_at',
            'created_at',
            'updated_at',
        )


class StudentSourceCollectionDetailSerializer(_CapabilityFeaturesMixin, StudentSourceCollectionListSerializer):
    sources = StudentSourceBriefSerializer(many=True, read_only=True)
    capabilities = serializers.SerializerMethodField()
    characters_summary = serializers.SerializerMethodField()

    class Meta(StudentSourceCollectionListSerializer.Meta):
        fields = StudentSourceCollectionListSerializer.Meta.fields + (
            'sources',
            'capabilities',
            'characters_summary',
        )

    @extend_schema_field(serializers.DictField())
    def get_capabilities(self, obj):
        return get_collection_character_capabilities(obj, features=self._features())

    @extend_schema_field(serializers.DictField())
    def get_characters_summary(self, obj):
        capabilities = get_collection_character_capabilities(obj, features=self._features())
        return {
            key: {
                'available': value['available'],
                'actions': value['actions'],
                'message': value['message'],
            }
            for key, value in capabilities.items()
        }


class StudentSourceCollectionCreateUpdateSerializer(serializers.ModelSerializer):
    # Accepts the Project's public_id (not its internal numeric pk) — see
    # StudentSourceBriefSerializer.project for why the read side matches.
    project = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=Project.objects.filter(is_deleted=False, status=Project.Status.ACTIVE),
        required=False,
        allow_null=True,
    )
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True, education_stage__is_active=True),
        required=False,
        allow_null=True,
    )
    name = serializers.CharField(
        required=True,
        allow_blank=False,
        error_messages={
            'required': 'يرجى إدخال اسم للمجلد.',
            'blank': 'يرجى إدخال اسم للمجلد.',
        },
    )

    class Meta:
        model = StudentSourceCollection
        fields = ('name', 'description', 'subject', 'project', 'color', 'icon', 'status')

    def validate_project(self, value):
        if value and value.owner_id != self.context['request'].user.id:
            raise serializers.ValidationError('Project not found or not owned by the current user.')
        return value

    def validate(self, attrs):
        # Blueprint 01_BACKEND.md §3.1: no collection may be created outside
        # a project. Only enforced on create -- partial_update reuses this
        # serializer (with partial=True) and must not force every PATCH to
        # resend a project the collection already has.
        if self.instance is None and not attrs.get('project'):
            raise serializers.ValidationError({'project': 'A project is required to create a folder.'})
        return attrs

    def create(self, validated_data):
        return StudentSourceCollection.objects.create(
            user=self.context['request'].user,
            **validated_data,
        )


class StudentSourceListSerializer(_CapabilityFeaturesMixin, serializers.ModelSerializer):
    subject = SubjectSerializer(read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    collection_name = serializers.CharField(source='collection.name', read_only=True)
    collection_id = serializers.IntegerField(source='collection.id', read_only=True)
    capabilities = serializers.SerializerMethodField()
    # See StudentSourceBriefSerializer.project — exposed as public_id, not the pk.
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = StudentSource
        # tuple[str, ...] so StudentSourceDetailSerializer.Meta can extend
        # this tuple without mypy flagging a fixed-length override.
        fields: tuple[str, ...] = (
            'id',
            'title',
            'description',
            'source_type',
            'project',
            'subject',
            'subject_name',
            'collection',
            'collection_id',
            'collection_name',
            'original_filename',
            'file_size',
            'mime_type',
            'extension',
            'status',
            'created_at',
            'updated_at',
            'capabilities',
        )

    @extend_schema_field(serializers.DictField())
    def get_capabilities(self, obj):
        capabilities = get_source_character_capabilities(obj, features=self._features())
        return {
            key: {
                'available': value['available'],
                'actions': value['actions'],
            }
            for key, value in capabilities.items()
        }


class StudentSourceDetailSerializer(StudentSourceListSerializer):
    file_url = serializers.SerializerMethodField()
    extracted_text_preview = serializers.SerializerMethodField()
    has_extracted_text = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()

    class Meta(StudentSourceListSerializer.Meta):
        fields = StudentSourceListSerializer.Meta.fields + (
            'file_url',
            'extracted_text_preview',
            'has_extracted_text',
            'processing_error',
            'metadata',
        )

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_file_url(self, obj):
        if not obj.file:
            return None
        request = self.context.get('request')
        return reverse('student-source-download', kwargs={'pk': obj.pk}, request=request)

    @extend_schema_field(serializers.CharField())
    def get_extracted_text_preview(self, obj):
        if not obj.extracted_text:
            return ''
        return obj.extracted_text[:500]

    @extend_schema_field(serializers.BooleanField())
    def get_has_extracted_text(self, obj):
        return bool(obj.extracted_text)

    @extend_schema_field(serializers.DictField())
    def get_capabilities(self, obj):
        return get_source_character_capabilities(obj, features=self._features())


class StudentSourceCreateSerializer(serializers.ModelSerializer):
    # Accepts the Project's public_id (not its internal numeric pk) — see
    # StudentSourceBriefSerializer.project for why the read side matches.
    project = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=Project.objects.filter(is_deleted=False, status=Project.Status.ACTIVE),
        required=False,
        allow_null=True,
    )
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True, education_stage__is_active=True),
        required=False,
        allow_null=True,
    )
    collection = serializers.PrimaryKeyRelatedField(
        queryset=StudentSourceCollection.objects.all(),
        required=False,
        allow_null=True,
    )
    title = serializers.CharField(
        required=True,
        allow_blank=False,
        error_messages={
            'required': 'يرجى إدخال عنوان للمصدر.',
            'blank': 'يرجى إدخال عنوان للمصدر.',
        },
    )
    file = serializers.FileField(
        required=True,
        allow_empty_file=False,
        error_messages={
            'required': 'يرجى اختيار ملف لرفعه.',
            'empty': 'الملف فارغ. يرجى اختيار ملف صالح.',
        },
    )

    class Meta:
        model = StudentSource
        fields = ('id', 'title', 'description', 'project', 'subject', 'collection', 'file')
        read_only_fields = ('id',)

    def validate_collection(self, value):
        if value and value.user_id != self.context['request'].user.id:
            raise serializers.ValidationError('المجلد غير موجود أو لا تملك صلاحية استخدامه.')
        return value

    def validate_project(self, value):
        if value and value.owner_id != self.context['request'].user.id:
            raise serializers.ValidationError('Project not found or not owned by the current user.')
        return value

    def validate_file(self, value):
        self.context['file_metadata'] = validate_student_source_file(value)
        return value

    def validate(self, attrs):
        collection = attrs.get('collection')
        project = attrs.get('project')
        if collection and attrs.get('subject') is None and collection.subject_id:
            attrs['subject'] = collection.subject
        if collection and collection.project_id:
            if project and project.id != collection.project_id:
                raise serializers.ValidationError({'project': 'Project must match the selected collection project.'})
            attrs['project'] = collection.project
        elif not project:
            # Blueprint 01_BACKEND.md §3.1: no source may be uploaded outside
            # a project.
            raise serializers.ValidationError({'project': 'A project is required to upload a source.'})
        return attrs

    def create(self, validated_data):
        file_metadata = self.context['file_metadata']
        Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
        return StudentSource.objects.create(
            user=self.context['request'].user,
            original_filename=file_metadata['original_filename'],
            file_size=file_metadata['file_size'],
            mime_type=file_metadata['mime_type'],
            extension=file_metadata['extension'],
            source_type=file_metadata['source_type'],
            **validated_data,
        )


class StudentSourceUpdateSerializer(serializers.ModelSerializer):
    # Accepts the Project's public_id (not its internal numeric pk) — see
    # StudentSourceBriefSerializer.project for why the read side matches.
    project = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=Project.objects.filter(is_deleted=False, status=Project.Status.ACTIVE),
        required=False,
        allow_null=True,
    )
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True, education_stage__is_active=True),
        required=False,
        allow_null=True,
    )
    collection = serializers.PrimaryKeyRelatedField(
        queryset=StudentSourceCollection.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = StudentSource
        fields = ('title', 'description', 'project', 'subject', 'collection')

    def validate_collection(self, value):
        if value and value.user_id != self.context['request'].user.id:
            raise serializers.ValidationError('المجلد غير موجود أو لا تملك صلاحية استخدامه.')
        return value

    def validate_project(self, value):
        if value and value.owner_id != self.context['request'].user.id:
            raise serializers.ValidationError('Project not found or not owned by the current user.')
        return value

    def validate(self, attrs):
        collection = attrs.get('collection', getattr(self.instance, 'collection', None))
        project = attrs.get('project', getattr(self.instance, 'project', None))
        if collection and collection.project_id:
            if project and project.id != collection.project_id:
                raise serializers.ValidationError({'project': 'Project must match the selected collection project.'})
            attrs['project'] = collection.project
        return attrs


class UseWithCharacterSerializer(serializers.Serializer):
    character = serializers.ChoiceField(choices=StudentSourceInteraction.Character.choices)
    action = serializers.ChoiceField(
        choices=StudentSourceInteraction.Action.choices,
        required=False,
        allow_blank=True,
    )


# The process action does not return a bare source: it wraps the (unchanged,
# still pre-processing) source alongside the Arabic message explaining that
# the work was queued rather than performed. Declaring that shape as a real
# serializer -- rather than patching it into the generated contract
# afterwards, as scripts/generate_api_contracts.py used to -- keeps
# `manage.py spectacular --validate` able to catch drift between this
# endpoint's code and its published schema.
class SourceProcessingQueuedResponseSerializer(serializers.Serializer):
    """Actual response returned by StudentSourceViewSet.process."""

    message = serializers.CharField()
    # Every DRF Serializer is itself a Field, and Field.source is a real
    # (differently-typed) base attribute -- the DRF metaclass intercepts
    # declared fields before that collision ever matters at runtime, but
    # mypy still sees it as an incompatible override of the base attribute.
    # Same treatment as AIJobCreateSerializer.source in apps/ai_integration.
    source = StudentSourceDetailSerializer()  # type: ignore[assignment]


class SourceCharacterResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    available = serializers.BooleanField(required=False)
    message = serializers.CharField()
    interaction = StudentSourceInteractionSerializer(required=False)
    advice = serializers.ListField(child=serializers.CharField(), required=False)
    study_plan = StudyPlanListSerializer(required=False)
    quiz = QuizListSerializer(required=False)
