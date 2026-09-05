from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.common.models import BaseModel, SoftDeleteModel

from .constants import (
    BILLING_INTERVAL_CUSTOM,
    BILLING_INTERVAL_FREE,
    BILLING_INTERVAL_LIFETIME,
    BILLING_INTERVAL_MONTHLY,
    BILLING_INTERVAL_YEARLY,
)


class SubscriptionPlan(BaseModel):
    class BillingInterval(models.TextChoices):
        FREE = BILLING_INTERVAL_FREE, 'Free'
        MONTHLY = BILLING_INTERVAL_MONTHLY, 'Monthly'
        YEARLY = BILLING_INTERVAL_YEARLY, 'Yearly'
        LIFETIME = BILLING_INTERVAL_LIFETIME, 'Lifetime'
        CUSTOM = BILLING_INTERVAL_CUSTOM, 'Custom'

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default='USD')
    billing_interval = models.CharField(
        max_length=20,
        choices=BillingInterval.choices,
        default=BillingInterval.FREE,
    )
    features = models.JSONField(default=dict, blank=True)
    limits = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('sort_order', 'price', 'id')
        verbose_name = 'Subscription Plan'
        verbose_name_plural = 'Subscription Plans'

    def __str__(self):
        return f'{self.name} ({self.code})'


class UserSubscription(SoftDeleteModel):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        TRIALING = 'trialing', 'Trialing'
        EXPIRED = 'expired', 'Expired'
        CANCELED = 'canceled', 'Canceled'
        PAST_DUE = 'past_due', 'Past Due'
        PAUSED = 'paused', 'Paused'

    class Provider(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        LOCAL = 'local', 'Local'
        PROMO = 'promo', 'Promo'
        STRIPE = 'stripe', 'Stripe'
        OTHER = 'other', 'Other'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription',
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name='user_subscriptions',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    started_at = models.DateTimeField(default=timezone.now)
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField(null=True, blank=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=False)
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.LOCAL)
    provider_subscription_id = models.CharField(max_length=255, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-updated_at',)
        verbose_name = 'User Subscription'
        verbose_name_plural = 'User Subscriptions'

    def __str__(self):
        return f'{self.user.email} - {self.plan.code}'


class SubscriptionUsage(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription_usages',
    )
    subscription = models.ForeignKey(
        UserSubscription,
        on_delete=models.SET_NULL,
        related_name='usage_periods',
        null=True,
        blank=True,
    )
    period_start = models.DateField()
    period_end = models.DateField()
    sources_uploaded = models.PositiveIntegerField(default=0)
    collections_created = models.PositiveIntegerField(default=0)
    ai_requests_used = models.PositiveIntegerField(default=0)
    khota_requests = models.PositiveIntegerField(default=0)
    fahes_requests = models.PositiveIntegerField(default=0)
    rasheed_requests = models.PositiveIntegerField(default=0)
    kholasa_requests = models.PositiveIntegerField(default=0)
    sada_requests = models.PositiveIntegerField(default=0)
    storage_used_bytes = models.PositiveBigIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-period_start',)
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'period_start', 'period_end'],
                name='unique_subscription_usage_period',
            )
        ]
        verbose_name = 'Subscription Usage'
        verbose_name_plural = 'Subscription Usage'

    def __str__(self):
        return f'{self.user.email} - {self.period_start:%Y-%m}'


class UsageLedgerEntry(BaseModel):
    """Immutable business ledger for pooled AI usage units.

    ``SubscriptionUsage`` remains a fast projection for existing clients, while
    this ledger is the replayable source for every reserve/commit/refund action.
    """

    class Operation(models.TextChoices):
        RESERVE = 'reserve', 'Reserve'
        COMMIT = 'commit', 'Commit'
        REFUND = 'refund', 'Refund'
        ADJUSTMENT = 'adjustment', 'Adjustment'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='usage_ledger_entries',
    )
    subscription = models.ForeignKey(
        UserSubscription,
        on_delete=models.SET_NULL,
        related_name='usage_ledger_entries',
        null=True,
        blank=True,
    )
    job = models.ForeignKey(
        'ai_integration.AIJob',
        on_delete=models.SET_NULL,
        related_name='usage_ledger_entries',
        null=True,
        blank=True,
    )
    period_start = models.DateField()
    period_end = models.DateField()
    task_type = models.CharField(max_length=50, blank=True, db_index=True)
    operation = models.CharField(max_length=20, choices=Operation.choices, db_index=True)
    units = models.PositiveIntegerField(default=1)
    idempotency_key = models.CharField(max_length=128)
    policy_version = models.CharField(max_length=40, default='usage-v1')
    actor_type = models.CharField(max_length=30, default='system')
    reason = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-created_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'operation', 'idempotency_key'],
                name='unique_usage_ledger_operation_key',
            ),
            models.CheckConstraint(condition=Q(units__gt=0), name='usage_ledger_units_positive'),
        ]
        indexes = [
            models.Index(fields=['user', 'period_start', '-created_at'], name='usage_ledger_user_period_idx'),
            models.Index(fields=['job', 'operation'], name='usage_ledger_job_operation_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('UsageLedgerEntry is immutable; create an adjustment entry instead.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('UsageLedgerEntry is immutable; create an adjustment entry instead.')


class SubscriptionEvent(models.Model):
    class EventType(models.TextChoices):
        CREATED = 'created', 'Created'
        PLAN_CHANGED = 'plan_changed', 'Plan Changed'
        CANCELED = 'canceled', 'Canceled'
        RENEWED = 'renewed', 'Renewed'
        EXPIRED = 'expired', 'Expired'
        LIMIT_REACHED = 'limit_reached', 'Limit Reached'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription_events',
    )
    subscription = models.ForeignKey(
        UserSubscription,
        on_delete=models.SET_NULL,
        related_name='events',
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=40, choices=EventType.choices)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = 'Subscription Event'
        verbose_name_plural = 'Subscription Events'

    def __str__(self):
        return f'{self.event_type} - {self.user.email}'
