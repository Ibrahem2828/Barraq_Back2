from collections.abc import Sequence

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import filters, permissions, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_dashboard.permissions import HasAdminPermission, IsAdminDashboardUser
from apps.admin_dashboard.services import log_admin_action

from .models import SubscriptionPlan, SubscriptionUsage, UserSubscription
from .serializers import (
    MySubscriptionSerializer,
    PublicSubscriptionPlanSerializer,
    SubscriptionPlanSerializer,
    SubscriptionUsageSerializer,
    UserSubscriptionSerializer,
    UserSubscriptionUpdateSerializer,
    serialize_my_subscription,
)
from .services import change_user_plan


@extend_schema(tags=['Subscriptions'])
class MySubscriptionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=MySubscriptionSerializer)
    def get(self, request):
        return Response(serialize_my_subscription(request.user, context={'request': request}))


@extend_schema(tags=['Subscriptions'])
class PublicSubscriptionPlanViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PublicSubscriptionPlanSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['code', 'name', 'description']
    ordering_fields = ['sort_order', 'price', 'code']
    ordering = ['sort_order', 'price']

    def get_queryset(self):
        return SubscriptionPlan.objects.filter(is_active=True, is_public=True)


# Every subclass combining this mixin with a viewsets base needs
# `# type: ignore[misc]` on its class line: mypy's cross-base override
# check treats the mixin and the DRF base as unrelated, even though the
# annotated types above are compatible. Standard DRF composition pattern.
class AdminSubscriptionPermissionMixin:
    permission_classes: Sequence[type[BasePermission]] = [IsAdminDashboardUser, HasAdminPermission]
    permission_map: dict[str, str] = {}
    required_permission: str | None = None

    def get_required_permission(self):
        return self.permission_map.get(getattr(self, 'action', None), self.required_permission)


@extend_schema(tags=['Admin Subscriptions'])
class AdminSubscriptionPlanViewSet(AdminSubscriptionPermissionMixin, viewsets.ModelViewSet):  # type: ignore[misc]
    serializer_class = SubscriptionPlanSerializer
    permission_map = {
        'list': 'subscription_plans.view',
        'retrieve': 'subscription_plans.view',
        'create': 'subscription_plans.create',
        'partial_update': 'subscription_plans.update',
        'destroy': 'subscription_plans.delete',
    }
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['code', 'name', 'description']
    ordering_fields = ['sort_order', 'price', 'code', 'created_at']
    ordering = ['sort_order', 'price']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = SubscriptionPlan.objects.all()
        is_active = self.request.query_params.get('is_active')
        if is_active in {'true', 'false'}:
            queryset = queryset.filter(is_active=is_active == 'true')
        is_public = self.request.query_params.get('is_public')
        if is_public in {'true', 'false'}:
            queryset = queryset.filter(is_public=is_public == 'true')
        billing_interval = self.request.query_params.get('billing_interval')
        if billing_interval:
            queryset = queryset.filter(billing_interval=billing_interval)
        return queryset

    def perform_create(self, serializer):
        plan = serializer.save()
        log_admin_action(self.request.user, 'subscription_plan.created', plan, request=self.request)

    def perform_update(self, serializer):
        plan = serializer.save()
        log_admin_action(self.request.user, 'subscription_plan.updated', plan, request=self.request)

    def destroy(self, request, *args, **kwargs):
        plan = self.get_object()
        if plan.code == 'free':
            raise ValidationError('The default free plan cannot be deleted.')
        if plan.user_subscriptions.exists():
            plan.is_active = False
            plan.save(update_fields=['is_active', 'updated_at'])
            log_admin_action(request.user, 'subscription_plan.deactivated', plan, request=request)
            return Response(status=status.HTTP_204_NO_CONTENT)
        log_admin_action(request.user, 'subscription_plan.deleted', plan, request=request)
        return super().destroy(request, *args, **kwargs)


@extend_schema(tags=['Admin Subscriptions'])
class AdminUserSubscriptionViewSet(AdminSubscriptionPermissionMixin, viewsets.ModelViewSet):  # type: ignore[misc]
    serializer_class = UserSubscriptionSerializer
    permission_map = {
        'list': 'subscriptions.view',
        'retrieve': 'subscriptions.view',
        'partial_update': 'subscriptions.update',
    }
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__email', 'user__full_name', 'plan__code', 'plan__name']
    ordering_fields = ['created_at', 'updated_at', 'current_period_end']
    ordering = ['-updated_at']
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        queryset = UserSubscription.objects.select_related('user', 'plan')
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)
        plan = self.request.query_params.get('plan')
        if plan:
            queryset = queryset.filter(plan__code=plan)
        user = self.request.query_params.get('user')
        if user:
            queryset = queryset.filter(user_id=user)
        return queryset

    def get_serializer_class(self):
        if self.action == 'partial_update':
            return UserSubscriptionUpdateSerializer
        return UserSubscriptionSerializer

    def partial_update(self, request, *args, **kwargs):
        subscription = self.get_object()
        serializer = self.get_serializer(subscription, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        plan = serializer.validated_data.pop('plan', None)
        if plan is not None:
            subscription = change_user_plan(
                subscription.user,
                plan,
                actor=request.user,
                status=serializer.validated_data.get('status'),
                current_period_end=serializer.validated_data.get('current_period_end'),
                metadata=serializer.validated_data.get('metadata'),
            )
        else:
            for field, value in serializer.validated_data.items():
                setattr(subscription, field, value)
            subscription.save()
        log_admin_action(request.user, 'subscription.updated', subscription, request=request)
        return Response(UserSubscriptionSerializer(subscription, context={'request': request}).data)


@extend_schema(tags=['Admin Subscriptions'])
class AdminSubscriptionUsageViewSet(AdminSubscriptionPermissionMixin, viewsets.ReadOnlyModelViewSet):  # type: ignore[misc]
    serializer_class = SubscriptionUsageSerializer
    required_permission = 'subscriptions.view'
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__email', 'user__full_name']
    ordering_fields = ['period_start', 'period_end', 'sources_uploaded', 'ai_requests_used']
    ordering = ['-period_start']

    def get_queryset(self):
        queryset = SubscriptionUsage.objects.select_related('user', 'subscription', 'subscription__plan')
        user = self.request.query_params.get('user')
        if user:
            queryset = queryset.filter(user_id=user)
        plan = self.request.query_params.get('plan')
        if plan:
            queryset = queryset.filter(subscription__plan__code=plan)
        return queryset


@extend_schema(tags=['Subscriptions'], responses=OpenApiTypes.OBJECT)
class SubscriptionApiRootView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        base = request.build_absolute_uri('/').rstrip('/')
        return Response({
            'plans': f'{base}/api/v1/subscriptions/plans/',
            'my_subscription': f'{base}/api/v1/subscriptions/me/',
        })
