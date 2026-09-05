from rest_framework import serializers

from .models import EducationStage, Subject, UserSubject


class EducationStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = EducationStage
        fields = (
            'id',
            'name',
            'description',
            'order',
            'is_active',
            'created_at',
            'updated_at',
        )


class SubjectSerializer(serializers.ModelSerializer):
    education_stage_name = serializers.CharField(
        source='education_stage.name',
        read_only=True,
    )

    class Meta:
        model = Subject
        # tuple[str, ...] (not an inferred fixed-length literal) so that
        # subclasses extending or replacing this Meta as `class Meta(Base.Meta)`
        # can declare a differently-shaped `fields` tuple without mypy treating
        # it as an incompatible override -- see SubjectSummarySerializer.
        fields: tuple[str, ...] = (
            'id',
            'name',
            'education_stage',
            'education_stage_name',
            'grade_level',
            'description',
            'is_active',
            'created_at',
            'updated_at',
        )


class UserSubjectWriteSerializer(serializers.ModelSerializer):
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True, education_stage__is_active=True)
    )

    class Meta:
        model = UserSubject
        fields = ('id', 'subject', 'created_at')
        read_only_fields = ('id', 'created_at')

    def validate_subject(self, value):
        user = self.context['request'].user
        if UserSubject.objects.filter(user=user, subject=value).exists():
            raise serializers.ValidationError('Subject is already assigned to this user.')
        return value

    def create(self, validated_data):
        return UserSubject.objects.create(
            user=self.context['request'].user,
            **validated_data,
        )


class UserSubjectReadSerializer(serializers.ModelSerializer):
    subject = SubjectSerializer(read_only=True)

    class Meta:
        model = UserSubject
        fields = ('id', 'subject', 'created_at')
