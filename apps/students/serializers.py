from rest_framework import serializers

from apps.subjects.models import EducationStage

from .models import StudentProfile


class StudentProfileSerializer(serializers.ModelSerializer):
    education_stage_name = serializers.CharField(
        source='education_stage.name',
        read_only=True,
    )

    class Meta:
        model = StudentProfile
        fields = (
            'id',
            'education_stage',
            'education_stage_name',
            'grade_level',
            'specialization',
            'study_goal',
            'daily_study_hours',
            'is_setup_completed',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'education_stage_name',
            'is_setup_completed',
            'created_at',
            'updated_at',
        )


class StudentProfileSetupSerializer(StudentProfileSerializer):
    education_stage = serializers.PrimaryKeyRelatedField(
        queryset=EducationStage.objects.filter(is_active=True),
        required=True,
    )
    grade_level = serializers.CharField(max_length=50, required=True, allow_blank=False)
