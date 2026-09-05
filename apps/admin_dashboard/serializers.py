from django.contrib.auth import get_user_model
from django.db.models import Count
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.quizzes.models import Quiz, QuizAttempt
from apps.sources.models import (
    StudentSource,
    StudentSourceCollection,
    StudentSourceInteraction,
)
from apps.students.models import StudentProfile
from apps.study_plans.models import StudyPlan, StudyTask

from .models import AdminPermission, AdminRole, AdminUserRole, AuditLog
from .services import (
    SECTION_PERMISSIONS,
    get_user_admin_permissions,
    is_super_admin_user,
)

User = get_user_model()


class UserBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'full_name', 'role', 'is_active')


class AdminPermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminPermission
        fields = (
            'id',
            'code',
            'name',
            'description',
            'category',
            'is_active',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')


class AdminRoleSerializer(serializers.ModelSerializer):
    permissions = AdminPermissionSerializer(many=True, read_only=True)
    permission_ids = serializers.PrimaryKeyRelatedField(
        queryset=AdminPermission.objects.filter(is_active=True),
        many=True,
        source='permissions',
        write_only=True,
        required=False,
    )
    permission_codes = serializers.SlugRelatedField(
        queryset=AdminPermission.objects.filter(is_active=True),
        many=True,
        slug_field='code',
        source='permissions',
        write_only=True,
        required=False,
    )

    class Meta:
        model = AdminRole
        fields = (
            'id',
            'name',
            'code',
            'description',
            'is_system',
            'is_active',
            'permissions',
            'permission_ids',
            'permission_codes',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'is_system', 'created_at', 'updated_at')

    def validate(self, attrs):
        request = self.context.get('request')
        actor = request.user if request else None
        instance = self.instance
        if instance and instance.code == 'super_admin' and not is_super_admin_user(actor):
            raise serializers.ValidationError('Only Super Admin can update Super Admin role.')
        if instance and instance.is_system and attrs.get('permissions') == []:
            raise serializers.ValidationError('System roles cannot have an empty permissions set.')
        return attrs

    def create(self, validated_data):
        permissions = validated_data.pop('permissions', [])
        role = AdminRole.objects.create(**validated_data)
        if permissions:
            role.permissions.set(permissions)
        return role

    def update(self, instance, validated_data):
        permissions = validated_data.pop('permissions', None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if permissions is not None:
            instance.permissions.set(permissions)
        return instance


class AdminUserRoleSerializer(serializers.ModelSerializer):
    role = AdminRoleSerializer(read_only=True)
    assigned_by = UserBriefSerializer(read_only=True)

    class Meta:
        model = AdminUserRole
        fields = ('id', 'role', 'assigned_by', 'assigned_at', 'is_active')


class AdminUserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'full_name',
            'phone_number',
            'role',
            'is_active',
            'is_staff',
            'is_superuser',
            'roles',
            'permissions',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields

    @extend_schema_field(AdminRoleSerializer(many=True))
    def get_roles(self, obj):
        roles = AdminRole.objects.filter(
            user_roles__user=obj,
            user_roles__is_active=True,
            is_active=True,
        ).distinct()
        return AdminRoleSerializer(roles, many=True).data

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_permissions(self, obj):
        return sorted(get_user_admin_permissions(obj))


class AdminUserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    role_ids = serializers.PrimaryKeyRelatedField(
        queryset=AdminRole.objects.filter(is_active=True),
        many=True,
        required=False,
        write_only=True,
    )
    role_codes = serializers.SlugRelatedField(
        queryset=AdminRole.objects.filter(is_active=True),
        many=True,
        slug_field='code',
        required=False,
        write_only=True,
    )

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'full_name',
            'phone_number',
            'password',
            'role_ids',
            'role_codes',
            'is_staff',
            'is_superuser',
        )
        read_only_fields = ('id',)

    def validate(self, attrs):
        request = self.context['request']
        roles = attrs.get('role_ids') or attrs.get('role_codes') or []
        if (attrs.get('is_superuser') or any(role.code == 'super_admin' for role in roles)) and not is_super_admin_user(
            request.user
        ):
            raise serializers.ValidationError('Only Super Admin can create Super Admin users.')
        if not is_super_admin_user(request.user):
            actor_permissions = get_user_admin_permissions(request.user)
            for role in roles:
                role_permissions = set(role.permissions.values_list('code', flat=True))
                if not role_permissions.issubset(actor_permissions):
                    raise serializers.ValidationError('Cannot assign roles with permissions you do not have.')
        return attrs


class AdminUserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('full_name', 'phone_number', 'is_active', 'is_staff', 'is_superuser')

    def validate(self, attrs):
        request = self.context['request']
        if is_super_admin_user(self.instance) and not is_super_admin_user(request.user):
            raise serializers.ValidationError('Only Super Admin can update Super Admin users.')
        if 'is_superuser' in attrs and not is_super_admin_user(request.user):
            raise serializers.ValidationError('Only Super Admin can change is_superuser.')
        return attrs


class AssignRolesSerializer(serializers.Serializer):
    role_ids = serializers.PrimaryKeyRelatedField(
        queryset=AdminRole.objects.filter(is_active=True),
        many=True,
        required=False,
    )
    role_codes = serializers.SlugRelatedField(
        queryset=AdminRole.objects.filter(is_active=True),
        slug_field='code',
        many=True,
        required=False,
    )

    def validate(self, attrs):
        roles = attrs.get('role_ids') or attrs.get('role_codes') or []
        if not roles:
            raise serializers.ValidationError('At least one role is required.')
        request = self.context['request']
        target_user = self.context['target_user']
        if is_super_admin_user(target_user) and not is_super_admin_user(request.user):
            raise serializers.ValidationError('Only Super Admin can change Super Admin roles.')
        if any(role.code == 'super_admin' for role in roles) and not is_super_admin_user(request.user):
            raise serializers.ValidationError('Only Super Admin can assign Super Admin role.')
        if not is_super_admin_user(request.user):
            actor_permissions = get_user_admin_permissions(request.user)
            for role in roles:
                role_permissions = set(role.permissions.values_list('code', flat=True))
                if not role_permissions.issubset(actor_permissions):
                    raise serializers.ValidationError('Cannot assign roles with permissions you do not have.')
        attrs['roles'] = roles
        return attrs


class StudentProfileBriefSerializer(serializers.ModelSerializer):
    education_stage_name = serializers.CharField(source='education_stage.name', read_only=True)

    class Meta:
        model = StudentProfile
        fields = (
            'education_stage',
            'education_stage_name',
            'grade_level',
            'specialization',
            'study_goal',
            'daily_study_hours',
            'is_setup_completed',
        )


class ManagedUserSerializer(serializers.ModelSerializer):
    student_profile = StudentProfileBriefSerializer(read_only=True)
    counts = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'full_name',
            'phone_number',
            'role',
            'is_active',
            'is_staff',
            'created_at',
            'updated_at',
            'student_profile',
            'counts',
        )

    @extend_schema_field(serializers.DictField(child=serializers.IntegerField()))
    def get_counts(self, obj):
        return {
            'collections_count': getattr(obj, 'collections_count', 0),
            'sources_count': getattr(obj, 'sources_count', 0),
            'study_plans_count': getattr(obj, 'study_plans_count', 0),
            'quizzes_count': getattr(obj, 'quizzes_count', 0),
        }


class ManagedUserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('full_name', 'phone_number', 'is_active')


class AdminMeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    full_name = serializers.CharField()
    role = serializers.CharField()
    roles = AdminRoleSerializer(many=True)
    permissions = serializers.ListField(child=serializers.CharField())
    is_superuser = serializers.BooleanField()
    is_staff = serializers.BooleanField()
    allowed_sections = serializers.DictField(child=serializers.BooleanField())


class AdminOverviewSerializer(serializers.Serializer):
    users_count = serializers.IntegerField()
    students_count = serializers.IntegerField()
    admins_count = serializers.IntegerField()
    sources_count = serializers.IntegerField()
    collections_count = serializers.IntegerField()
    study_plans_count = serializers.IntegerField()
    quizzes_count = serializers.IntegerField()
    quiz_attempts_count = serializers.IntegerField()
    character_interactions_count = serializers.IntegerField()
    new_users_today = serializers.IntegerField()
    new_users_this_week = serializers.IntegerField()
    new_sources_this_week = serializers.IntegerField()
    subscriptions_count = serializers.IntegerField()
    ai_jobs_count = serializers.IntegerField()
    ai_jobs_pending_count = serializers.IntegerField()
    ai_jobs_failed_count = serializers.IntegerField()
    ai_jobs_completed_count = serializers.IntegerField()
    ai_feedback_count = serializers.IntegerField()
    ai_average_rating = serializers.FloatField(allow_null=True)
    notifications_count = serializers.IntegerField()
    unread_notifications_count = serializers.IntegerField()
    support_tickets_count = serializers.IntegerField()
    open_support_tickets_count = serializers.IntegerField()
    active_subscriptions_count = serializers.IntegerField(allow_null=True)
    free_users_count = serializers.IntegerField()
    premium_users_count = serializers.IntegerField()
    pro_users_count = serializers.IntegerField()
    school_users_count = serializers.IntegerField()
    subscriptions_by_plan = serializers.ListField()
    system_health = serializers.DictField()


class AIUsageTotalsSerializer(serializers.Serializer):
    job_count = serializers.IntegerField()
    completed_count = serializers.IntegerField()
    input_tokens = serializers.IntegerField()
    output_tokens = serializers.IntegerField()
    total_tokens = serializers.IntegerField()
    cost_usd = serializers.DecimalField(max_digits=12, decimal_places=6)


class AIUsageMonthToDateSerializer(serializers.Serializer):
    year_month = serializers.CharField()
    job_count = serializers.IntegerField()
    cost_usd = serializers.DecimalField(max_digits=12, decimal_places=6)


class AIUsageDailyPointSerializer(serializers.Serializer):
    date = serializers.DateField()
    job_count = serializers.IntegerField()
    cost_usd = serializers.DecimalField(max_digits=12, decimal_places=6)
    total_tokens = serializers.IntegerField()


class AIUsageByCharacterSerializer(serializers.Serializer):
    character = serializers.CharField()
    job_count = serializers.IntegerField()
    cost_usd = serializers.DecimalField(max_digits=12, decimal_places=6)
    total_tokens = serializers.IntegerField()


class AdminAIUsageSerializer(serializers.Serializer):
    range_days = serializers.IntegerField()
    totals = AIUsageTotalsSerializer()
    month_to_date = AIUsageMonthToDateSerializer()
    daily = AIUsageDailyPointSerializer(many=True)
    by_character = AIUsageByCharacterSerializer(many=True)


class SystemHealthSerializer(serializers.Serializer):
    database = serializers.CharField()
    cache = serializers.CharField()
    storage = serializers.CharField()
    media_root_exists = serializers.BooleanField()
    media_root_writable = serializers.BooleanField()
    static_root_exists = serializers.BooleanField()
    ai_service_enabled = serializers.BooleanField()
    app_name = serializers.CharField()
    app_version = serializers.CharField()
    app_phase = serializers.CharField()
    environment = serializers.CharField()
    debug = serializers.BooleanField()
    allowed_hosts_count = serializers.IntegerField()


class AuditLogSerializer(serializers.ModelSerializer):
    actor = UserBriefSerializer(read_only=True)

    class Meta:
        model = AuditLog
        fields = (
            'id',
            'actor',
            'action',
            'target_type',
            'target_id',
            'metadata',
            'ip_address',
            'user_agent',
            'created_at',
        )
        read_only_fields = fields


class AdminStudentSourceCollectionSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    source_count = serializers.IntegerField(read_only=True)
    total_file_size = serializers.IntegerField(read_only=True)

    class Meta:
        model = StudentSourceCollection
        fields = (
            'id',
            'user',
            'name',
            'description',
            'subject',
            'subject_name',
            'color',
            'icon',
            'status',
            'source_count',
            'total_file_size',
            'created_at',
            'updated_at',
        )


class AdminStudentSourceSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    collection_name = serializers.CharField(source='collection.name', read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = StudentSource
        fields = (
            'id',
            'user',
            'title',
            'description',
            'source_type',
            'subject',
            'subject_name',
            'collection',
            'collection_name',
            'original_filename',
            'file_size',
            'mime_type',
            'extension',
            'status',
            'file_url',
            'created_at',
            'updated_at',
        )

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_file_url(self, obj):
        if not obj.file:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(obj.file.url) if request else obj.file.url


class AdminStudyTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudyTask
        fields = ('id', 'title', 'task_date', 'estimated_minutes', 'priority', 'status', 'order')


class AdminStudyPlanSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    tasks = AdminStudyTaskSerializer(many=True, read_only=True)

    class Meta:
        model = StudyPlan
        fields = (
            'id',
            'user',
            'title',
            'description',
            'subject',
            'subject_name',
            'start_date',
            'end_date',
            'daily_study_minutes',
            'goal',
            'difficulty_level',
            'status',
            'generation_type',
            'completion_percentage',
            'created_at',
            'updated_at',
            'tasks',
        )


class AdminQuizSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    attempts_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Quiz
        fields = (
            'id',
            'user',
            'title',
            'description',
            'subject',
            'subject_name',
            'topic',
            'difficulty_level',
            'quiz_type',
            'generation_type',
            'status',
            'questions_count',
            'time_limit_minutes',
            'attempts_count',
            'created_at',
            'updated_at',
        )


class AdminQuizAttemptSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    quiz_title = serializers.CharField(source='quiz.title', read_only=True)

    class Meta:
        model = QuizAttempt
        fields = (
            'id',
            'user',
            'quiz',
            'quiz_title',
            'status',
            'started_at',
            'submitted_at',
            'score',
            'max_score',
            'percentage',
            'correct_answers_count',
            'wrong_answers_count',
            'unanswered_count',
            'duration_seconds',
            'created_at',
            'updated_at',
        )


class AdminCharacterInteractionSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)
    source_title = serializers.CharField(source='source.title', read_only=True)
    collection_name = serializers.CharField(source='collection.name', read_only=True)

    class Meta:
        model = StudentSourceInteraction
        fields = (
            'id',
            'user',
            'source',
            'source_title',
            'collection',
            'collection_name',
            'character',
            'action',
            'status',
            'result_type',
            'result_id',
            'message',
            'metadata',
            'created_at',
            'updated_at',
        )


def build_admin_me_payload(user):
    roles = AdminRole.objects.filter(
        user_roles__user=user,
        user_roles__is_active=True,
        is_active=True,
    ).distinct()
    permissions = sorted(get_user_admin_permissions(user))
    permission_set = set(permissions)
    return {
        'id': user.id,
        'email': user.email,
        'full_name': user.full_name,
        'role': user.role,
        'roles': roles,
        'permissions': permissions,
        'is_superuser': user.is_superuser,
        'is_staff': user.is_staff,
        'allowed_sections': {
            section: permission in permission_set
            for section, permission in SECTION_PERMISSIONS.items()
        },
    }


def managed_user_queryset():
    return User.objects.annotate(
        collections_count=Count('student_source_collections', distinct=True),
        sources_count=Count('student_sources', distinct=True),
        study_plans_count=Count('study_plans', distinct=True),
        quizzes_count=Count('quizzes', distinct=True),
    )
