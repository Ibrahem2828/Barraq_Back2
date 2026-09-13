from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.projects.models import Project
from apps.subjects.models import Subject
from apps.subjects.serializers import SubjectSerializer

from .models import StudyPlan, StudyTask
from .services import (
    create_manual_plan,
    create_plan_task,
    update_plan,
    update_task,
)


class SubjectSummarySerializer(SubjectSerializer):
    class Meta(SubjectSerializer.Meta):
        fields = (
            'id',
            'name',
            'education_stage',
            'education_stage_name',
            'grade_level',
            'description',
            'is_active',
        )


class StudyTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudyTask
        # tuple[str, ...] so TodayTaskSerializer.Meta can extend this tuple
        # without mypy flagging a fixed-length override.
        fields: tuple[str, ...] = (
            'id',
            'title',
            'description',
            'task_date',
            'estimated_minutes',
            'priority',
            'status',
            'order',
            'completed_at',
        )


class StudyPlanListSerializer(serializers.ModelSerializer):
    subject = SubjectSummarySerializer(read_only=True)
    total_tasks = serializers.SerializerMethodField()
    completed_tasks = serializers.SerializerMethodField()
    # Exposed as the Project's public_id (not its internal numeric pk) — matches
    # the ?project=<public_id> filter this list already accepts.
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = StudyPlan
        # tuple[str, ...] so StudyPlanDetailSerializer.Meta can extend this
        # tuple without mypy flagging a fixed-length override.
        fields: tuple[str, ...] = (
            'id',
            'title',
            'project',
            'subject',
            'start_date',
            'end_date',
            'status',
            'difficulty_level',
            'generation_type',
            'completion_percentage',
            'total_tasks',
            'completed_tasks',
        )

    @extend_schema_field(serializers.IntegerField())
    def get_total_tasks(self, obj):
        return getattr(obj, 'total_tasks', obj.tasks.count())

    @extend_schema_field(serializers.IntegerField())
    def get_completed_tasks(self, obj):
        return getattr(
            obj,
            'completed_tasks',
            obj.tasks.filter(status=StudyTask.Status.COMPLETED).count(),
        )


class StudyPlanDetailSerializer(StudyPlanListSerializer):
    tasks = StudyTaskSerializer(many=True, read_only=True)

    class Meta(StudyPlanListSerializer.Meta):
        fields = StudyPlanListSerializer.Meta.fields + (
            'description',
            'daily_study_minutes',
            'goal',
            'ai_request_id',
            'created_at',
            'updated_at',
            'tasks',
        )


class StudyPlanCreateSerializer(serializers.ModelSerializer):
    # Accepts the Project's public_id (not its internal numeric pk) — see
    # StudyPlanListSerializer.project for why the read side matches.
    project = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=Project.objects.filter(is_deleted=False, status=Project.Status.ACTIVE),
        required=False,
        allow_null=True,
    )
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True, education_stage__is_active=True)
    )

    class Meta:
        model = StudyPlan
        fields = (
            'project',
            'title',
            'description',
            'subject',
            'start_date',
            'end_date',
            'daily_study_minutes',
            'goal',
            'difficulty_level',
            'generation_type',
        )

    def validate(self, attrs):
        if attrs['start_date'] > attrs['end_date']:
            raise serializers.ValidationError(
                {'end_date': 'End date must be greater than or equal to start date.'}
            )
        project = attrs.get('project')
        user = self.context['request'].user
        if not project:
            # Blueprint 01_BACKEND.md §3.1: no study plan -- manual or
            # AI-generated -- may be created outside a project.
            raise serializers.ValidationError({'project': 'A project is required to create a study plan.'})
        if project.owner_id != user.id:
            raise serializers.ValidationError({'project': 'Project not found or not owned by the current user.'})
        if project.subject_id and attrs['subject'].id != project.subject_id:
            raise serializers.ValidationError({'subject': 'Subject must match the selected project.'})
        return attrs

    def create(self, validated_data):
        user = self.context['request'].user
        generation_type = validated_data.pop(
            'generation_type',
            StudyPlan.GenerationType.MANUAL,
        )

        if generation_type == StudyPlan.GenerationType.AI:
            raise serializers.ValidationError({
                'generation_type': 'Use POST /api/v1/ai/jobs/ with task_type=khota_generate_plan for AI plans.'
            })
        return create_manual_plan(user, validated_data)


class StudyPlanUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudyPlan
        fields = (
            'title',
            'description',
            'status',
            'goal',
            'daily_study_minutes',
            'difficulty_level',
        )

    def validate_status(self, value):
        plan = self.instance
        if (
            value == StudyPlan.Status.COMPLETED
            and plan.tasks.exclude(status=StudyTask.Status.COMPLETED).exists()
        ):
            raise serializers.ValidationError(
                'All tasks must be completed before marking the plan as completed.'
            )
        return value

    def update(self, instance, validated_data):
        return update_plan(instance, validated_data)


class StudyTaskCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudyTask
        fields = (
            'id',
            'title',
            'description',
            'task_date',
            'estimated_minutes',
            'priority',
            'order',
        )
        read_only_fields = ('id',)

    def validate(self, attrs):
        plan = self.context['plan']
        task_date = attrs['task_date']
        if task_date < plan.start_date or task_date > plan.end_date:
            raise serializers.ValidationError(
                {'task_date': 'Task date must fall within the plan date range.'}
            )

        if StudyTask.objects.filter(
            plan=plan,
            task_date=task_date,
            order=attrs['order'],
        ).exists():
            raise serializers.ValidationError(
                {'order': 'A task with this order already exists for that day.'}
            )
        return attrs

    def create(self, validated_data):
        return create_plan_task(self.context['plan'], validated_data)


class StudyTaskUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudyTask
        fields = (
            'title',
            'description',
            'task_date',
            'estimated_minutes',
            'priority',
            'status',
            'order',
        )

    def validate(self, attrs):
        task = self.instance
        plan = task.plan
        task_date = attrs.get('task_date', task.task_date)
        order = attrs.get('order', task.order)

        if task_date < plan.start_date or task_date > plan.end_date:
            raise serializers.ValidationError(
                {'task_date': 'Task date must fall within the plan date range.'}
            )

        if StudyTask.objects.filter(
            plan=plan,
            task_date=task_date,
            order=order,
        ).exclude(pk=task.pk).exists():
            raise serializers.ValidationError(
                {'order': 'A task with this order already exists for that day.'}
            )

        return attrs

    def update(self, instance, validated_data):
        return update_task(instance, validated_data)


class TaskStatusUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudyTask
        fields = ('id', 'status', 'completed_at')
        read_only_fields = fields


class StudyPlanMiniSerializer(serializers.ModelSerializer):
    subject = SubjectSummarySerializer(read_only=True)

    class Meta:
        model = StudyPlan
        fields = ('id', 'title', 'status', 'subject')


class TodayTaskSerializer(StudyTaskSerializer):
    plan = StudyPlanMiniSerializer(read_only=True)

    class Meta(StudyTaskSerializer.Meta):
        fields = StudyTaskSerializer.Meta.fields + ('plan',)


class TaskSummarySerializer(serializers.Serializer):
    total_tasks = serializers.IntegerField()
    completed_tasks = serializers.IntegerField()
    pending_tasks = serializers.IntegerField()
    total_estimated_minutes = serializers.IntegerField()


class TodayPlanResponseSerializer(serializers.Serializer):
    date = serializers.DateField()
    summary = TaskSummarySerializer()
    tasks = TodayTaskSerializer(many=True)


class WeekDayTasksSerializer(serializers.Serializer):
    date = serializers.DateField()
    summary = TaskSummarySerializer()
    tasks = TodayTaskSerializer(many=True)


class WeekPlanResponseSerializer(serializers.Serializer):
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    days = WeekDayTasksSerializer(many=True)
