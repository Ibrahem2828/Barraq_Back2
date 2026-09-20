from collections.abc import Sequence
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_integration.models import AIFeedback, AIJob
from apps.common.health import cache_status, database_status, storage_status
from apps.notifications.models import Notification
from apps.organizations import scope as scope_policy
from apps.quizzes.models import Quiz, QuizAttempt
from apps.sources.models import (
    StudentSource,
    StudentSourceCollection,
    StudentSourceInteraction,
)
from apps.study_plans.models import StudyPlan
from apps.subscriptions.models import UserSubscription
from apps.subscriptions.serializers import (
    AdminChangeSubscriptionSerializer,
    UserSubscriptionSerializer,
)
from apps.subscriptions.services import (
    cancel_user_subscription,
    change_user_plan,
)
from apps.support.models import SupportTicket

from .models import AdminPermission, AdminRole, AuditLog
from .permissions import HasAdminPermission, IsAdminDashboardUser
from .serializers import (
    AdminAIUsageSerializer,
    AdminCharacterInteractionSerializer,
    AdminMeSerializer,
    AdminOverviewSerializer,
    AdminPermissionSerializer,
    AdminQuizAttemptSerializer,
    AdminQuizSerializer,
    AdminRoleSerializer,
    AdminStudentSourceCollectionSerializer,
    AdminStudentSourceSerializer,
    AdminStudyPlanSerializer,
    AdminUserCreateSerializer,
    AdminUserSerializer,
    AdminUserUpdateSerializer,
    AssignRolesSerializer,
    AuditLogSerializer,
    ManagedUserSerializer,
    ManagedUserUpdateSerializer,
    SystemHealthSerializer,
    build_admin_me_payload,
    managed_user_queryset,
)
from .services import (
    assign_roles_to_user,
    count_super_admins,
    is_super_admin_user,
    log_admin_action,
)

User = get_user_model()


# Every subclass combining this mixin with a viewsets/APIView base needs
# `# type: ignore[misc]` on its class line: mypy's cross-base override
# check treats the mixin and the DRF base as unrelated, even though the
# annotated types above are compatible. Standard DRF composition pattern.
class AdminPermissionMixin(scope_policy.TenantScopedQuerysetMixin):
    permission_classes: Sequence[type[BasePermission]] = [IsAdminDashboardUser, HasAdminPermission]
    permission_map: dict[str, str] = {}
    required_permission: str | None = None

    def get_required_permission(self):
        return self.permission_map.get(getattr(self, 'action', None), self.required_permission)


def apply_date_filters(queryset, params, field='created_at'):
    date_value = params.get(field)
    date_from = params.get(f'{field}_from')
    date_to = params.get(f'{field}_to')
    if date_value:
        parsed = parse_date(date_value)
        if parsed:
            queryset = queryset.filter(**{f'{field}__date': parsed})
    if date_from:
        parsed = parse_date(date_from)
        if parsed:
            queryset = queryset.filter(**{f'{field}__date__gte': parsed})
    if date_to:
        parsed = parse_date(date_to)
        if parsed:
            queryset = queryset.filter(**{f'{field}__date__lte': parsed})
    return queryset


@extend_schema(tags=['Admin Dashboard'])
class AdminMeView(APIView):
    permission_classes = [IsAdminDashboardUser]

    @extend_schema(responses=AdminMeSerializer)
    def get(self, request):
        serializer = AdminMeSerializer(build_admin_me_payload(request.user))
        return Response(serializer.data)


@extend_schema(tags=['Admin Dashboard'])
class AdminOverviewView(APIView):
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = 'dashboard.view'

    @extend_schema(responses=AdminOverviewSerializer)
    def get(self, request):
        # Platform totals, not tenant totals. A scoped manager reads their
        # own numbers from /admin/organizations/<id>/overview/ instead: a
        # count describes the shape of every tenant it covers, so handing a
        # global one to a scoped account leaks exactly what scoping hides.
        scope_policy.assert_global_scope(request.user)
        today = timezone.localdate()
        week_start = today - timezone.timedelta(days=today.weekday())
        payload = {
            'users_count': User.objects.count(),
            'students_count': User.objects.filter(role=User.Roles.STUDENT).count(),
            'admins_count': User.objects.filter(
                Q(is_superuser=True)
                | Q(admin_user_roles__is_active=True, admin_user_roles__role__is_active=True)
            ).distinct().count(),
            'sources_count': StudentSource.objects.count(),
            'collections_count': StudentSourceCollection.objects.count(),
            'study_plans_count': StudyPlan.objects.count(),
            'quizzes_count': Quiz.objects.count(),
            'quiz_attempts_count': QuizAttempt.objects.count(),
            'character_interactions_count': StudentSourceInteraction.objects.count(),
            'new_users_today': User.objects.filter(created_at__date=today).count(),
            'new_users_this_week': User.objects.filter(created_at__date__gte=week_start).count(),
            'new_sources_this_week': StudentSource.objects.filter(created_at__date__gte=week_start).count(),
            'subscriptions_count': UserSubscription.objects.count(),
            'active_subscriptions_count': UserSubscription.objects.filter(status=UserSubscription.Status.ACTIVE).count(),
            'free_users_count': UserSubscription.objects.filter(plan__code='free').count(),
            'premium_users_count': UserSubscription.objects.filter(plan__code='premium').count(),
            'pro_users_count': UserSubscription.objects.filter(plan__code='pro').count(),
            'school_users_count': UserSubscription.objects.filter(plan__code='school').count(),
            'ai_jobs_count': AIJob.objects.count(),
            'ai_jobs_pending_count': AIJob.objects.filter(status__in=[AIJob.Status.CREATED, AIJob.Status.QUEUED, AIJob.Status.SUBMITTED, AIJob.Status.PROCESSING, AIJob.Status.VALIDATING]).count(),
            'ai_jobs_failed_count': AIJob.objects.filter(status=AIJob.Status.FAILED).count(),
            'ai_jobs_completed_count': AIJob.objects.filter(status=AIJob.Status.COMPLETED).count(),
            'ai_feedback_count': AIFeedback.objects.count(),
            'ai_average_rating': AIFeedback.objects.aggregate(value=Avg('rating'))['value'],
            'notifications_count': Notification.objects.count(),
            'unread_notifications_count': Notification.objects.filter(read_at__isnull=True).count(),
            'support_tickets_count': SupportTicket.objects.count(),
            'open_support_tickets_count': SupportTicket.objects.exclude(status__in=[SupportTicket.Status.RESOLVED, SupportTicket.Status.CLOSED]).count(),
            'subscriptions_by_plan': list(
                UserSubscription.objects.values('plan__code', 'plan__name')
                .annotate(count=Count('id'))
                .order_by('plan__code')
            ),
            'system_health': build_system_health(),
        }
        return Response(payload)


@extend_schema(tags=['Admin Dashboard'])
class AdminAIUsageView(APIView):
    """AI token/cost consumption, denormalized onto AIJob from the AI
    service's completion webhook (metadata.usage/.provider) -- see
    apps/ai_integration/services.py:complete_job. Numbers reflect what
    Django has received, not a live query against the AI service's own
    database.
    """

    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = 'analytics.view'

    @extend_schema(responses=AdminAIUsageSerializer)
    def get(self, request):
        # Same reasoning as the overview: platform-wide AI usage is not a
        # scoped account's to read.
        scope_policy.assert_global_scope(request.user)
        try:
            range_days = int(request.query_params.get('days', 30))
        except (TypeError, ValueError):
            range_days = 30
        range_days = min(max(range_days, 1), 180)

        today = timezone.localdate()
        range_start = today - timezone.timedelta(days=range_days - 1)
        year_month = today.strftime('%Y-%m')
        month_start = today.replace(day=1)

        base = AIJob.objects.filter(created_at__date__gte=range_start)
        totals = base.aggregate(
            job_count=Count('id'),
            completed_count=Count('id', filter=Q(status=AIJob.Status.COMPLETED)),
            input_tokens=Sum('input_tokens'),
            output_tokens=Sum('output_tokens'),
            cost_usd=Sum('cost_usd'),
        )
        month_to_date = AIJob.objects.filter(created_at__date__gte=month_start).aggregate(
            job_count=Count('id'),
            cost_usd=Sum('cost_usd'),
        )
        daily = list(
            base.annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(
                job_count=Count('id'),
                cost_usd=Sum('cost_usd'),
                total_tokens=Sum('input_tokens') + Sum('output_tokens'),
            )
            .order_by('day')
        )
        by_character = list(
            base.values('character')
            .annotate(
                job_count=Count('id'),
                cost_usd=Sum('cost_usd'),
                total_tokens=Sum('input_tokens') + Sum('output_tokens'),
            )
            .order_by('-cost_usd')
        )
        payload = {
            'range_days': range_days,
            'totals': {
                'job_count': totals['job_count'] or 0,
                'completed_count': totals['completed_count'] or 0,
                'input_tokens': totals['input_tokens'] or 0,
                'output_tokens': totals['output_tokens'] or 0,
                'total_tokens': (totals['input_tokens'] or 0) + (totals['output_tokens'] or 0),
                'cost_usd': totals['cost_usd'] or 0,
            },
            'month_to_date': {
                'year_month': year_month,
                'job_count': month_to_date['job_count'] or 0,
                'cost_usd': month_to_date['cost_usd'] or 0,
            },
            'daily': [
                {
                    'date': row['day'],
                    'job_count': row['job_count'],
                    'cost_usd': row['cost_usd'] or 0,
                    'total_tokens': row['total_tokens'] or 0,
                }
                for row in daily
            ],
            'by_character': [
                {
                    'character': row['character'],
                    'job_count': row['job_count'],
                    'cost_usd': row['cost_usd'] or 0,
                    'total_tokens': row['total_tokens'] or 0,
                }
                for row in by_character
            ],
        }
        return Response(payload)


@extend_schema(tags=['Admin Permissions'])
class AdminPermissionViewSet(  # type: ignore[misc]
    AdminPermissionMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    # Platform catalogue: a permission code names an action, never a tenant.
    tenant_user_field = None
    serializer_class = AdminPermissionSerializer
    required_permission = 'roles.view'
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['code', 'name', 'description', 'category']
    ordering_fields = ['code', 'category', 'created_at']
    ordering = ['category', 'code']

    def get_queryset(self):
        queryset = AdminPermission.objects.all()
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category__iexact=category)
        return queryset


@extend_schema(tags=['Admin Roles'])
class AdminRoleViewSet(AdminPermissionMixin, viewsets.ModelViewSet):  # type: ignore[misc]
    # Platform catalogue: a role definition is not tenant data.
    tenant_user_field = None
    serializer_class = AdminRoleSerializer
    permission_map = {
        'list': 'roles.view',
        'retrieve': 'roles.view',
        'create': 'roles.create',
        'partial_update': 'roles.update',
        'destroy': 'roles.delete',
    }
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['code', 'name', 'description']
    ordering_fields = ['name', 'code', 'created_at']
    ordering = ['name']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return AdminRole.objects.prefetch_related('permissions')

    def perform_create(self, serializer):
        role = serializer.save()
        log_admin_action(self.request.user, 'role.created', role, request=self.request)

    def perform_update(self, serializer):
        role = serializer.save()
        log_admin_action(self.request.user, 'role.updated', role, request=self.request)

    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        if role.is_system or role.code == 'super_admin':
            raise ValidationError('System roles cannot be deleted.')
        log_admin_action(request.user, 'role.deleted', role, request=request)
        return super().destroy(request, *args, **kwargs)


@extend_schema(tags=['Admin Users'])
class AdminUserViewSet(AdminPermissionMixin, viewsets.ModelViewSet):  # type: ignore[misc]
    # Staff directory, narrowed to admins whose scope overlaps the caller's.
    tenant_user_field = 'admins'
    permission_map = {
        'list': 'admins.view',
        'retrieve': 'admins.view',
        'create': 'admins.create',
        'partial_update': 'admins.update',
        'destroy': 'admins.delete',
        'assign_roles': 'admins.assign_roles',
        'revoke_roles': 'admins.assign_roles',
    }
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['email', 'full_name', 'phone_number']
    ordering_fields = ['created_at', 'email', 'full_name']
    ordering = ['-created_at']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = User.objects.filter(
            Q(is_superuser=True)
            | Q(is_staff=True)
            | Q(admin_user_roles__is_active=True)
        ).distinct().prefetch_related('admin_user_roles__role__permissions')
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(admin_user_roles__role__code=role, admin_user_roles__is_active=True)
        is_active = self.request.query_params.get('is_active')
        if is_active in {'true', 'false'}:
            queryset = queryset.filter(is_active=is_active == 'true')
        # "Who supervises this class / this school."
        #
        # The filter is resolved through the caller's own scope, not merely
        # applied alongside it. Matching a scope row directly would answer a
        # question the caller may not ask: an account working in two schools
        # is legitimately visible to a manager of one of them, and filtering
        # by the other school would confirm that second grant exists.
        organization = self.request.query_params.get('organization')
        if organization:
            queryset = self._filter_by_scope_target(queryset, 'organization', organization)
        classroom = self.request.query_params.get('classroom')
        if classroom:
            queryset = self._filter_by_scope_target(queryset, 'classroom', classroom)
        return apply_date_filters(queryset.distinct(), self.request.query_params)

    def _filter_by_scope_target(self, queryset, kind, public_id):
        from apps.organizations.models import Classroom, Organization

        if kind == 'organization':
            target = Organization.objects.filter(public_id=public_id).first()
            reachable = scope_policy.accessible_organization_ids(self.request.user)
        else:
            target = Classroom.objects.filter(public_id=public_id).first()
            reachable = scope_policy.accessible_classroom_ids(self.request.user)

        if target is None:
            return queryset.none()
        if not scope_policy.is_unrestricted(reachable) and target.id not in (reachable or set()):
            # Asking about a tenant the caller cannot reach answers nothing,
            # rather than answering about the people they happen to share.
            return queryset.none()
        return queryset.filter(
            admin_user_roles__is_active=True,
            **{f'admin_user_roles__scopes__{kind}__public_id': public_id},
        )

    def get_serializer_class(self):
        if self.action == 'create':
            return AdminUserCreateSerializer
        if self.action == 'partial_update':
            return AdminUserUpdateSerializer
        if self.action == 'assign_roles':
            return AssignRolesSerializer
        return AdminUserSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        roles = data.pop('role_ids', None) or data.pop('role_codes', [])
        password = data.pop('password')
        is_superuser = data.pop('is_superuser', False)
        # Popped so they never reach create_user as model fields; the
        # serializer already refused any scope the operator cannot grant.
        resolved_scopes = data.pop('resolved_scopes', None)
        data.pop('scopes', None)
        data.setdefault('is_staff', True)
        data['role'] = User.Roles.SUPER_ADMIN if is_superuser else User.Roles.ADMIN
        user = User.objects.create_user(password=password, is_superuser=is_superuser, **data)
        assign_roles_to_user(user, roles, assigned_by=request.user, scopes=resolved_scopes)
        log_admin_action(
            request.user,
            'admin.created',
            user,
            {'roles': [role.code for role in roles]},
            request,
        )
        return Response(AdminUserSerializer(user, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        target = self.get_object()
        serializer = self.get_serializer(target, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        log_admin_action(request.user, 'admin.updated', user, request=request)
        return Response(AdminUserSerializer(user, context=self.get_serializer_context()).data)

    def destroy(self, request, *args, **kwargs):
        target = self.get_object()
        if target.pk == request.user.pk:
            raise ValidationError('Admins cannot delete themselves.')
        if is_super_admin_user(target) and not is_super_admin_user(request.user):
            raise PermissionDenied('Only Super Admin can delete Super Admin users.')
        if is_super_admin_user(target) and count_super_admins(exclude_user=target) == 0:
            raise ValidationError('Cannot delete the last Super Admin.')
        log_admin_action(request.user, 'admin.deleted', target, request=request)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='assign-roles')
    def assign_roles(self, request, pk=None):
        target = self.get_object()
        serializer = self.get_serializer(data=request.data, context={**self.get_serializer_context(), 'target_user': target})
        serializer.is_valid(raise_exception=True)
        roles = serializer.validated_data['roles']
        # The serializer already refused any scope this operator cannot
        # grant; None means global, which is what it always meant.
        scopes = serializer.validated_data.get('scopes')
        assign_roles_to_user(target, roles, assigned_by=request.user, scopes=scopes)
        target.role = User.Roles.SUPER_ADMIN if any(role.code == 'super_admin' for role in roles) else User.Roles.ADMIN
        target.is_staff = True
        target.save(update_fields=['role', 'is_staff', 'updated_at'])
        log_admin_action(
            request.user,
            'admin.role_assigned',
            target,
            {
                'roles': [role.code for role in roles],
                'scopes': [scope['scope_type'] for scope in scopes] if scopes else ['global'],
            },
            request,
        )
        return Response(AdminUserSerializer(target, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'], url_path='revoke-roles')
    def revoke_roles(self, request, pk=None):
        """Withdraw this account's grants, bounded by the caller's own scope.

        Removing someone from an organization must not remove them from a
        different one. A scoped operator withdraws only the grants inside
        their reach; a platform operator withdraws all of them, which is what
        revocation meant before scope existed.
        """
        target = self.get_object()
        if is_super_admin_user(target) and not is_super_admin_user(request.user):
            raise ValidationError('Only Super Admin can change Super Admin roles.')
        removed = scope_policy.revoke_grants_within_scope(target, request.user)
        log_admin_action(
            request.user,
            'admin.roles_revoked',
            target,
            {'scopes_removed': removed},
            request,
        )
        return Response(AdminUserSerializer(target, context=self.get_serializer_context()).data)


@extend_schema(tags=['Admin Managed Users'])
class ManagedUserViewSet(  # type: ignore[misc]
    AdminPermissionMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    # Rows are the learners themselves, so the boundary is the pk.
    tenant_user_field = 'id'
    serializer_class = ManagedUserSerializer
    permission_map = {
        'list': 'users.view',
        'retrieve': 'users.view',
        'suspend': 'users.suspend',
        'activate': 'users.activate',
        'partial_update': 'users.update',
        'change_subscription': 'subscriptions.update',
        'cancel_subscription': 'subscriptions.cancel',
    }
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['email', 'full_name', 'phone_number']
    ordering_fields = ['created_at', 'email', 'full_name']
    ordering = ['-created_at']
    http_method_names = ['get', 'patch', 'post', 'head', 'options']

    def get_queryset(self):
        queryset = managed_user_queryset().select_related('student_profile', 'student_profile__education_stage')
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)
        is_active = self.request.query_params.get('is_active')
        if is_active in {'true', 'false'}:
            queryset = queryset.filter(is_active=is_active == 'true')
        return apply_date_filters(queryset, self.request.query_params)

    def get_serializer_class(self):
        if self.action == 'partial_update':
            return ManagedUserUpdateSerializer
        return ManagedUserSerializer

    def partial_update(self, request, *args, **kwargs):
        target = self.get_object()
        serializer = self.get_serializer(target, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        log_admin_action(request.user, 'user.updated', user, request=request)
        return Response(ManagedUserSerializer(user, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'], url_path='suspend')
    def suspend(self, request, pk=None):
        target = self.get_object()
        target.is_active = False
        target.save(update_fields=['is_active', 'updated_at'])
        log_admin_action(request.user, 'user.suspended', target, request=request)
        return Response(ManagedUserSerializer(target, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'], url_path='activate')
    def activate(self, request, pk=None):
        target = self.get_object()
        target.is_active = True
        target.save(update_fields=['is_active', 'updated_at'])
        log_admin_action(request.user, 'user.activated', target, request=request)
        return Response(ManagedUserSerializer(target, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'], url_path='change-subscription')
    def change_subscription(self, request, pk=None):
        target = self.get_object()
        serializer = AdminChangeSubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subscription = change_user_plan(
            target,
            serializer.validated_data['plan'],
            actor=request.user,
            status=serializer.validated_data.get('status'),
            current_period_end=serializer.validated_data.get('current_period_end'),
            metadata=serializer.validated_data.get('metadata'),
        )
        log_admin_action(
            request.user,
            'subscription.updated',
            subscription,
            {'user_id': target.id, 'plan': subscription.plan.code},
            request,
        )
        return Response(UserSubscriptionSerializer(subscription, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='cancel-subscription')
    def cancel_subscription(self, request, pk=None):
        target = self.get_object()
        subscription = cancel_user_subscription(target, actor=request.user)
        log_admin_action(
            request.user,
            'subscription.canceled',
            subscription,
            {'user_id': target.id, 'plan': subscription.plan.code},
            request,
        )
        return Response(UserSubscriptionSerializer(subscription, context={'request': request}).data)


class AdminReadOnlyViewSet(AdminPermissionMixin, viewsets.ReadOnlyModelViewSet):  # type: ignore[misc]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        return apply_date_filters(queryset, self.request.query_params)


@extend_schema(tags=['Admin Sources'])
class AdminSourceCollectionViewSet(AdminReadOnlyViewSet):
    tenant_user_field = 'user_id'
    serializer_class = AdminStudentSourceCollectionSerializer
    required_permission = 'collections.view'
    search_fields = ['name', 'description', 'user__email', 'user__full_name']
    ordering_fields = ['created_at', 'updated_at', 'name']
    ordering = ['-updated_at']

    def get_queryset(self):
        queryset = StudentSourceCollection.objects.select_related('user', 'subject').annotate(
            source_count=Count('sources', distinct=True),
        )
        user = self.request.query_params.get('user')
        if user:
            queryset = queryset.filter(user_id=user)
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        subject = self.request.query_params.get('subject')
        if subject:
            queryset = queryset.filter(subject_id=subject)
        return queryset


@extend_schema(tags=['Admin Sources'])
class AdminSourceViewSet(AdminPermissionMixin, viewsets.ModelViewSet):  # type: ignore[misc]
    tenant_user_field = 'user_id'
    serializer_class = AdminStudentSourceSerializer
    permission_map = {'list': 'sources.view', 'retrieve': 'sources.view', 'destroy': 'sources.delete'}
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'description', 'original_filename', 'user__email', 'user__full_name']
    ordering_fields = ['created_at', 'updated_at', 'file_size']
    ordering = ['-created_at']
    http_method_names = ['get', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = StudentSource.objects.select_related('user', 'subject', 'collection')
        user = self.request.query_params.get('user')
        if user:
            queryset = queryset.filter(user_id=user)
        for field in ('source_type', 'status', 'subject'):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field if field != 'subject' else 'subject_id': value})
        return apply_date_filters(queryset, self.request.query_params)

    def destroy(self, request, *args, **kwargs):
        source = self.get_object()
        file_field = source.file
        log_admin_action(request.user, 'source.deleted_by_admin', source, request=request)
        response = super().destroy(request, *args, **kwargs)
        if file_field:
            file_field.delete(save=False)
        return response


@extend_schema(tags=['Admin Study'])
class AdminStudyPlanViewSet(AdminReadOnlyViewSet):
    tenant_user_field = 'user_id'
    serializer_class = AdminStudyPlanSerializer
    required_permission = 'study_plans.view'
    search_fields = ['title', 'description', 'goal', 'user__email', 'user__full_name']
    ordering_fields = ['created_at', 'start_date', 'end_date']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = StudyPlan.objects.select_related('user', 'subject').prefetch_related('tasks')
        for field in ('status', 'subject', 'difficulty_level', 'generation_type'):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field if field != 'subject' else 'subject_id': value})
        user = self.request.query_params.get('user')
        if user:
            queryset = queryset.filter(user_id=user)
        return queryset


@extend_schema(tags=['Admin Quizzes'])
class AdminQuizViewSet(AdminReadOnlyViewSet):
    tenant_user_field = 'user_id'
    serializer_class = AdminQuizSerializer
    required_permission = 'quizzes.view'
    search_fields = ['title', 'description', 'topic', 'user__email', 'user__full_name']
    ordering_fields = ['created_at', 'questions_count']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = Quiz.objects.select_related('user', 'subject').annotate(attempts_count=Count('attempts', distinct=True))
        for field in ('status', 'subject', 'difficulty_level', 'quiz_type', 'generation_type'):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field if field != 'subject' else 'subject_id': value})
        user = self.request.query_params.get('user')
        if user:
            queryset = queryset.filter(user_id=user)
        return queryset


@extend_schema(tags=['Admin Quizzes'])
class AdminQuizAttemptViewSet(AdminReadOnlyViewSet):
    tenant_user_field = 'user_id'
    serializer_class = AdminQuizAttemptSerializer
    required_permission = 'quiz_attempts.view'
    search_fields = ['quiz__title', 'user__email', 'user__full_name']
    ordering_fields = ['started_at', 'submitted_at', 'percentage', 'created_at']
    ordering = ['-started_at']

    def get_queryset(self):
        queryset = QuizAttempt.objects.select_related('user', 'quiz')
        for field in ('status', 'quiz'):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field if field != 'quiz' else 'quiz_id': value})
        user = self.request.query_params.get('user')
        if user:
            queryset = queryset.filter(user_id=user)
        return queryset


@extend_schema(tags=['Admin Characters'])
class AdminCharacterInteractionViewSet(AdminReadOnlyViewSet):
    tenant_user_field = 'user_id'
    serializer_class = AdminCharacterInteractionSerializer
    required_permission = 'character_interactions.view'
    search_fields = ['message', 'user__email', 'user__full_name']
    ordering_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = StudentSourceInteraction.objects.select_related('user', 'source', 'collection')
        for field in ('character', 'action', 'status'):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        user = self.request.query_params.get('user')
        if user:
            queryset = queryset.filter(user_id=user)
        return queryset


@extend_schema(tags=['Admin Audit'])
class AuditLogViewSet(AdminReadOnlyViewSet):
    # An audit entry belongs to the tenant of whoever performed it.
    tenant_user_field = 'actor_id'
    serializer_class = AuditLogSerializer
    required_permission = 'audit_logs.view'
    search_fields = ['action', 'target_type', 'target_id', 'actor__email', 'actor__full_name']
    ordering_fields = ['created_at', 'action']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = AuditLog.objects.select_related('actor')
        action_name = self.request.query_params.get('action')
        if action_name:
            queryset = queryset.filter(action=action_name)
        actor = self.request.query_params.get('actor')
        if actor:
            queryset = queryset.filter(actor_id=actor)
        return queryset


@extend_schema(tags=['Admin System'])
class SystemHealthView(APIView):
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = 'system.health'

    @extend_schema(responses=SystemHealthSerializer)
    def get(self, request):
        return Response(build_system_health())


def build_system_health():
    media_root = Path(settings.MEDIA_ROOT)
    static_root = Path(settings.STATIC_ROOT)
    return {
        'database': database_status(),
        'cache': cache_status(),
        'storage': storage_status(),
        'media_root_exists': media_root.exists(),
        'media_root_writable': media_root.exists() and media_root.is_dir(),
        'static_root_exists': static_root.exists(),
        'ai_service_enabled': settings.AI_SERVICE_ENABLED,
        'app_name': settings.APP_NAME,
        'app_version': settings.APP_VERSION,
        'app_phase': settings.APP_PHASE,
        'environment': settings.ENVIRONMENT,
        'debug': settings.DEBUG,
        'allowed_hosts_count': len(settings.ALLOWED_HOSTS),
    }


@extend_schema(tags=['Admin Dashboard'], responses=OpenApiTypes.OBJECT)
class AdminApiRootView(APIView):
    permission_classes = [IsAdminDashboardUser]

    def get(self, request):
        base = request.build_absolute_uri('/').rstrip('/')
        return Response({
            'me': f'{base}/api/v1/admin/me/',
            'overview': f'{base}/api/v1/admin/overview/',
            'system_health': f'{base}/api/v1/admin/system/health/',
            'users': f'{base}/api/v1/admin/users/',
            'roles': f'{base}/api/v1/admin/roles/',
            'subjects': f'{base}/api/v1/admin/subjects/',
            'sources': f'{base}/api/v1/admin/sources/',
            'quizzes': f'{base}/api/v1/admin/quizzes/',
            'study_plans': f'{base}/api/v1/admin/study-plans/',
            'ai_jobs': f'{base}/api/v1/admin/ai-jobs/',
            'support_tickets': f'{base}/api/v1/admin/support-tickets/',
            'subscriptions': f'{base}/api/v1/admin/user-subscriptions/',
        })
