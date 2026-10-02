from rest_framework import serializers

from apps.projects.models import Project
from apps.sources.models import StudentSource
from apps.subjects.models import Subject

from .models import DeskNote, DeskPreference, FocusSession, ReadingPosition, SessionTask


class DeskNoteSerializer(serializers.ModelSerializer):
    source = serializers.PrimaryKeyRelatedField(read_only=True)  # type: ignore[assignment,var-annotated]
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = DeskNote
        fields = (
            'id', 'source', 'project', 'note_type', 'body', 'anchor', 'color', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')


class DeskNoteWriteSerializer(serializers.ModelSerializer):
    source = serializers.PrimaryKeyRelatedField(  # type: ignore[assignment]
        queryset=StudentSource.objects.all(), required=False, allow_null=True
    )
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(
        slug_field='public_id', queryset=Project.objects.filter(is_deleted=False), required=False, allow_null=True
    )

    class Meta:
        model = DeskNote
        fields = ('source', 'project', 'note_type', 'body', 'anchor', 'color')

    def validate_source(self, value):
        if value and value.user_id != self.context['request'].user.id:
            raise serializers.ValidationError('Source not found.')
        return value

    def validate_project(self, value):
        if value and value.owner_id != self.context['request'].user.id:
            raise serializers.ValidationError('Project not found.')
        return value


class ReadingPositionSerializer(serializers.ModelSerializer):
    source = serializers.PrimaryKeyRelatedField(read_only=True)  # type: ignore[assignment,var-annotated]
    source_title = serializers.CharField(source='source.title', read_only=True)
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(
        source='source.project', slug_field='public_id', read_only=True
    )

    class Meta:
        model = ReadingPosition
        fields = ('source', 'source_title', 'project', 'position', 'updated_at')
        read_only_fields = fields


class FocusSessionSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source='subject.name', read_only=True, default=None)
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = FocusSession
        fields = (
            'id', 'subject', 'subject_name', 'project', 'task_label', 'status',
            'started_at', 'ended_at', 'duration_seconds',
        )
        read_only_fields = ('id', 'status', 'started_at', 'ended_at', 'duration_seconds')


class FocusSessionStartSerializer(serializers.Serializer):
    subject: serializers.PrimaryKeyRelatedField = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True), required=False, allow_null=True
    )
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(
        slug_field='public_id', queryset=Project.objects.filter(is_deleted=False), required=False, allow_null=True
    )
    task_label = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')

    def validate_project(self, value):
        if value and value.owner_id != self.context['request'].user.id:
            raise serializers.ValidationError('Project not found.')
        return value


class SessionTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionTask
        fields = ('id', 'focus_session', 'task_date', 'title', 'is_done', 'order')
        read_only_fields = ('id',)

    def validate_focus_session(self, value):
        if value and value.user_id != self.context['request'].user.id:
            raise serializers.ValidationError('Focus session not found.')
        return value


class DeskPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeskPreference
        fields = ('theme', 'background', 'accent_color', 'favorite_character')


class ReadingPositionUpdateSerializer(serializers.Serializer):
    position = serializers.JSONField()


class DeskHomeSerializer(serializers.Serializer):
    """Documents DeskHomeView's aggregate payload for the OpenAPI schema."""

    continue_reading = ReadingPositionSerializer(allow_null=True)
    today_tasks = SessionTaskSerializer(many=True)
    running_focus_session = FocusSessionSerializer(allow_null=True)
    preferences = DeskPreferenceSerializer()
