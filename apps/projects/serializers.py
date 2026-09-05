from rest_framework import serializers

from apps.subjects.models import Subject

from .models import Project, ProjectActivity


class ProjectActivitySerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True)

    class Meta:
        model = ProjectActivity
        fields = (
            "id", "event_type", "request_id", "artifact_type", "artifact_id",
            "metadata", "actor_name", "created_at",
        )
        read_only_fields = fields


class ProjectSerializer(serializers.ModelSerializer):
    source_count = serializers.IntegerField(read_only=True)
    ai_job_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Project
        fields = (
            "public_id", "title", "subject", "goal", "education_context", "status",
            "color", "icon", "source_count", "ai_job_count", "created_at", "updated_at",
        )
        read_only_fields = ("public_id", "source_count", "ai_job_count", "created_at", "updated_at")

    def validate_subject(self, value):
        if value is not None and not value.is_active:
            raise serializers.ValidationError("The selected subject is not active.")
        return value


class ProjectCreateUpdateSerializer(ProjectSerializer):
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True), required=False, allow_null=True
    )
    title = serializers.CharField(max_length=255, trim_whitespace=True)

    def validate_title(self, value):
        if not value:
            raise serializers.ValidationError("Project title must not be blank.")
        return value
