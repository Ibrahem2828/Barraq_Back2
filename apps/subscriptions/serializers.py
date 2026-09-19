from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import SubscriptionEvent, SubscriptionPlan, SubscriptionUsage, UserSubscription
from .services import subscription_summary_for_user

User = get_user_model()


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = (
            'id',
            'code',
            'name',
            'description',
            'price',
            'currency',
            'billing_interval',
            'features',
            'limits',
            'is_active',
            'is_public',
            'sort_order',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')


class PublicSubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = (
            'id',
            'code',
            'name',
            'description',
            'price',
            'currency',
            'billing_interval',
            'features',
            'limits',
            'sort_order',
        )


class SubscriptionUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionUsage
        fields = (
            'id',
            'period_start',
            'period_end',
            'sources_uploaded',
            'collections_created',
            'ai_requests_used',
            'khota_requests',
            'fahes_requests',
            'rasheed_requests',
            'kholasa_requests',
            'sada_requests',
            'storage_used_bytes',
            'metadata',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields


class UserSubscriptionSerializer(serializers.ModelSerializer):
    user: serializers.PrimaryKeyRelatedField = serializers.PrimaryKeyRelatedField(read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_full_name = serializers.CharField(source='user.full_name', read_only=True)
    plan = SubscriptionPlanSerializer(read_only=True)
    plan_name = serializers.CharField(source='plan.name', read_only=True)
    plan_code = serializers.CharField(source='plan.code', read_only=True)

    class Meta:
        model = UserSubscription
        fields = (
            'id',
            'user',
            'user_email',
            'user_full_name',
            'plan',
            'plan_name',
            'plan_code',
            'status',
            'started_at',
            'current_period_start',
            'current_period_end',
            'trial_ends_at',
            'canceled_at',
            'auto_renew',
            'provider',
            'provider_subscription_id',
            'metadata',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')


class UserSubscriptionUpdateSerializer(serializers.ModelSerializer):
    plan_id = serializers.PrimaryKeyRelatedField(
        queryset=SubscriptionPlan.objects.filter(is_active=True),
        source='plan',
        required=False,
        write_only=True,
    )
    plan_code = serializers.SlugRelatedField(
        queryset=SubscriptionPlan.objects.filter(is_active=True),
        slug_field='code',
        source='plan',
        required=False,
        write_only=True,
    )

    class Meta:
        model = UserSubscription
        fields = (
            'plan_id',
            'plan_code',
            'status',
            'current_period_end',
            'trial_ends_at',
            'auto_renew',
            'provider',
            'provider_subscription_id',
            'metadata',
        )


class AdminChangeSubscriptionSerializer(serializers.Serializer):
    plan_id = serializers.PrimaryKeyRelatedField(
        queryset=SubscriptionPlan.objects.filter(is_active=True),
        required=False,
    )
    plan_code = serializers.SlugRelatedField(
        queryset=SubscriptionPlan.objects.filter(is_active=True),
        slug_field='code',
        required=False,
    )
    status = serializers.ChoiceField(choices=UserSubscription.Status.choices, required=False)
    current_period_end = serializers.DateTimeField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False)

    def validate(self, attrs):
        if not attrs.get('plan_id') and not attrs.get('plan_code'):
            raise serializers.ValidationError('plan_id or plan_code is required.')
        attrs['plan'] = attrs.get('plan_id') or attrs.get('plan_code')
        return attrs


class MySubscriptionSerializer(serializers.Serializer):
    plan = PublicSubscriptionPlanSerializer()
    subscription = UserSubscriptionSerializer()
    usage = SubscriptionUsageSerializer()
    limits = serializers.DictField()
    features = serializers.DictField()
    remaining = serializers.DictField()
    # The plan figures intersected with the platform ceiling. `limits` alone
    # is what a client must NOT display: a plan may advertise more than the
    # platform can accept. See services.effective_max_file_size_mb.
    effective_limits = serializers.DictField()


class SubscriptionEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionEvent
        fields = ('id', 'user', 'subscription', 'event_type', 'metadata', 'created_at')
        read_only_fields = fields


def serialize_my_subscription(user, context=None):
    summary = subscription_summary_for_user(user)
    return MySubscriptionSerializer(summary, context=context).data
