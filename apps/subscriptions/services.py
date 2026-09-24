import calendar

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.sources.models import StudentSource, StudentSourceCollection

from .constants import (
    CHARACTER_FEATURE_KEYS,
    CHARACTER_LIMIT_KEYS,
    CHARACTER_USAGE_FIELDS,
    DEFAULT_PLANS,
    FREE_PLAN_CODE,
)
from .exceptions import SubscriptionFeatureNotAllowed, SubscriptionLimitExceeded
from .models import (
    SubscriptionEvent,
    SubscriptionPlan,
    SubscriptionUsage,
    UsageLedgerEntry,
    UserSubscription,
)


def ensure_default_plans():
    plans = {}
    for code, payload in DEFAULT_PLANS.items():
        plan, _ = SubscriptionPlan.objects.update_or_create(
            code=code,
            defaults={
                'name': payload['name'],
                'description': payload['description'],
                'price': payload['price'],
                'currency': payload['currency'],
                'billing_interval': payload['billing_interval'],
                'features': payload['features'],
                'limits': payload['limits'],
                'is_active': True,
                'is_public': payload['is_public'],
                'sort_order': payload['sort_order'],
            },
        )
        plans[code] = plan
    return plans


def get_free_plan():
    plan = SubscriptionPlan.objects.filter(code=FREE_PLAN_CODE).first()
    if plan is None:
        plan = ensure_default_plans()[FREE_PLAN_CODE]
    return plan


def current_month_period(today=None):
    today = today or timezone.localdate()
    last_day = calendar.monthrange(today.year, today.month)[1]
    return today.replace(day=1), today.replace(day=last_day)


def get_or_create_user_subscription(user):
    subscription = UserSubscription.objects.select_related('plan').filter(user=user).first()
    if subscription:
        return subscription

    # UserSubscription.user is a OneToOneField, so a soft-deleted row still
    # occupies that unique slot. Revive it instead of racing an INSERT into
    # get_or_create() and hitting an IntegrityError on the unique constraint.
    deleted_subscription = UserSubscription.all_objects.filter(user=user, is_deleted=True).first()
    if deleted_subscription:
        deleted_subscription.is_deleted = False
        deleted_subscription.deleted_at = None
        deleted_subscription.plan = get_free_plan()
        deleted_subscription.status = UserSubscription.Status.ACTIVE
        deleted_subscription.provider = UserSubscription.Provider.LOCAL
        deleted_subscription.save(
            update_fields=['is_deleted', 'deleted_at', 'plan', 'status', 'provider', 'updated_at']
        )
        return deleted_subscription

    return UserSubscription.objects.get_or_create(
        user=user,
        defaults={
            'plan': get_free_plan(),
            'status': UserSubscription.Status.ACTIVE,
            'provider': UserSubscription.Provider.LOCAL,
        },
    )[0]


def get_user_subscription(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    return get_or_create_user_subscription(user)


def subscription_has_entitlement(subscription, now=None):
    if subscription is None:
        return False
    now = now or timezone.now()
    if subscription.status not in {UserSubscription.Status.ACTIVE, UserSubscription.Status.TRIALING}:
        return False
    if subscription.status == UserSubscription.Status.TRIALING and subscription.trial_ends_at and subscription.trial_ends_at <= now:
        return False
    return not (subscription.current_period_end and subscription.current_period_end <= now)


def get_user_plan(user):
    subscription = get_user_subscription(user)
    return subscription.plan if subscription_has_entitlement(subscription) else get_free_plan()


def get_user_limits(user):
    return dict(get_user_plan(user).limits or {})


def get_user_features(user):
    return dict(get_user_plan(user).features or {})


def get_or_create_current_usage(user):
    subscription = get_user_subscription(user)
    period_start, period_end = current_month_period()
    usage, _ = SubscriptionUsage.objects.get_or_create(
        user=user,
        period_start=period_start,
        period_end=period_end,
        defaults={'subscription': subscription},
    )
    if subscription and usage.subscription_id != subscription.id:
        usage.subscription = subscription
        usage.save(update_fields=['subscription', 'updated_at'])
    return usage


def get_current_usage(user):
    return get_or_create_current_usage(user)


def recalculate_storage_used(user):
    usage = get_or_create_current_usage(user)
    total = StudentSource.objects.filter(user=user).aggregate(total=Sum('file_size'))['total'] or 0
    if usage.storage_used_bytes != total:
        usage.storage_used_bytes = total
        usage.save(update_fields=['storage_used_bytes', 'updated_at'])
    return usage


def limit_value(limits, key):
    value = limits.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def bytes_to_mb(size_bytes):
    return size_bytes / (1024 * 1024)


def effective_max_file_size_mb(user):
    """The upload size a user can actually achieve, in MB.

    Two independent ceilings apply: the plan's `max_file_size_mb`, and the
    platform-wide STUDENT_SOURCE_MAX_UPLOAD_MB, which exists because the AI
    service reads a whole source into memory during ingestion and cannot be
    promised more than it can hold. The smaller one is what the user gets, so
    it is the only number a client should ever display -- showing the plan
    figure alone is how a Pro user came to be told 150MB when uploads over
    the platform cap were refused outright.
    """

    platform_limit = int(getattr(settings, 'STUDENT_SOURCE_MAX_UPLOAD_MB', 50))
    plan_limit = limit_value(get_user_limits(user), 'max_file_size_mb')
    if plan_limit is None:
        return platform_limit
    return min(plan_limit, platform_limit)


def get_remaining_limits(user):
    limits = get_user_limits(user)
    usage = recalculate_storage_used(user)
    actual_collections = StudentSourceCollection.objects.filter(user=user).count()
    actual_sources = StudentSource.objects.filter(user=user).count()
    remaining = {}
    pairs = {
        'max_collections': actual_collections,
        'max_sources': actual_sources,
        'max_storage_mb': bytes_to_mb(usage.storage_used_bytes),
        'max_ai_requests_per_month': usage.ai_requests_used,
        'max_khota_requests_per_month': usage.khota_requests,
        'max_fahes_requests_per_month': usage.fahes_requests,
        'max_rasheed_requests_per_month': usage.rasheed_requests,
        'max_kholasa_requests_per_month': usage.kholasa_requests,
        'max_sada_requests_per_month': usage.sada_requests,
    }
    for key, used in pairs.items():
        limit = limit_value(limits, key)
        remaining[key] = None if limit is None else max(limit - int(used), 0)
    return remaining


def _raise_limit(message, code, limit=None, usage=None):
    raise SubscriptionLimitExceeded(message, code=code, limit=limit, usage=usage)


def can_create_collection(user):
    features = get_user_features(user)
    if features.get('can_create_unlimited_collections'):
        return True
    limit = limit_value(get_user_limits(user), 'max_collections')
    if limit is None:
        return True
    usage = StudentSourceCollection.objects.filter(user=user).count()
    if usage >= limit:
        _raise_limit(
            'لقد وصلت إلى الحد الأقصى للمجلدات في خطتك الحالية.',
            'collection_limit_reached',
            limit,
            usage,
        )
    return True


def can_upload_source(user, file_size_bytes):
    limits = get_user_limits(user)
    source_limit = limit_value(limits, 'max_sources')
    source_count = StudentSource.objects.filter(user=user).count()
    if source_limit is not None and source_count >= source_limit:
        _raise_limit(
            'لقد وصلت إلى الحد الأقصى للمصادر في خطتك الحالية.',
            'source_limit_reached',
            source_limit,
            source_count,
        )

    # The effective limit, not the plan figure: the platform ceiling can be
    # lower than what a plan advertises, and rejecting here with the plan
    # number would quote a limit the upload never actually had. Reported as
    # one code so the client has a single case to handle.
    file_limit = effective_max_file_size_mb(user)
    if bytes_to_mb(file_size_bytes) > file_limit:
        _raise_limit(
            f'حجم الملف أكبر من الحد المسموح ({file_limit}MB).',
            'file_size_limit_exceeded',
            file_limit,
            round(bytes_to_mb(file_size_bytes), 2),
        )

    storage_limit = limit_value(limits, 'max_storage_mb')
    if storage_limit is not None:
        current_storage = StudentSource.objects.filter(user=user).aggregate(total=Sum('file_size'))['total'] or 0
        new_storage_mb = bytes_to_mb(current_storage + file_size_bytes)
        if new_storage_mb > storage_limit:
            _raise_limit(
                'لقد وصلت إلى حد التخزين المتاح في خطتك الحالية.',
                'storage_limit_exceeded',
                storage_limit,
                round(new_storage_mb, 2),
            )
    return True


def can_use_character(user, character):
    features = get_user_features(user)
    feature_key = CHARACTER_FEATURE_KEYS.get(character)
    if feature_key and not features.get(feature_key, False):
        raise SubscriptionFeatureNotAllowed(
            'هذه الشخصية غير متاحة في خطتك الحالية.',
            code='character_not_allowed',
            character=character,
        )

    limits = get_user_limits(user)
    usage = get_or_create_current_usage(user)
    limit_key = CHARACTER_LIMIT_KEYS.get(character)
    usage_field = CHARACTER_USAGE_FIELDS.get(character)
    if limit_key and usage_field:
        limit = limit_value(limits, limit_key)
        used = getattr(usage, usage_field)
        if limit is not None and used >= limit:
            _raise_limit(
                f'لقد استهلكت حد استخدام {character} لهذا الشهر.',
                'character_limit_reached',
                limit,
                used,
            )
    return True


def consume_collection_created(user):
    usage = get_or_create_current_usage(user)
    usage.collections_created += 1
    usage.save(update_fields=['collections_created', 'updated_at'])
    return usage


def consume_source_uploaded(user, file_size_bytes):
    usage = get_or_create_current_usage(user)
    usage.sources_uploaded += 1
    usage.storage_used_bytes = StudentSource.objects.filter(user=user).aggregate(total=Sum('file_size'))['total'] or 0
    usage.save(update_fields=['sources_uploaded', 'storage_used_bytes', 'updated_at'])
    return usage


def consume_character_request(user, character):
    usage = get_or_create_current_usage(user)
    field = CHARACTER_USAGE_FIELDS.get(character)
    if field:
        setattr(usage, field, getattr(usage, field) + 1)
        usage.ai_requests_used += 1
        usage.save(update_fields=[field, 'ai_requests_used', 'updated_at'])
    return usage


@transaction.atomic
def change_user_plan(user, plan, actor=None, status=None, current_period_end=None, metadata=None):
    subscription = get_or_create_user_subscription(user)
    old_plan = subscription.plan
    subscription.plan = plan
    if status:
        subscription.status = status
    else:
        subscription.status = UserSubscription.Status.ACTIVE
    subscription.current_period_start = timezone.now()
    subscription.current_period_end = current_period_end
    subscription.canceled_at = None
    subscription.metadata = {**(subscription.metadata or {}), **(metadata or {})}
    subscription.save()
    SubscriptionEvent.objects.create(
        user=user,
        subscription=subscription,
        event_type=SubscriptionEvent.EventType.PLAN_CHANGED,
        metadata={
            'old_plan': old_plan.code,
            'new_plan': plan.code,
            'actor_id': getattr(actor, 'id', None),
            **(metadata or {}),
        },
    )
    return subscription


@transaction.atomic
def cancel_user_subscription(user, actor=None):
    subscription = get_or_create_user_subscription(user)
    subscription.status = UserSubscription.Status.CANCELED
    subscription.canceled_at = timezone.now()
    subscription.auto_renew = False
    subscription.save(update_fields=['status', 'canceled_at', 'auto_renew', 'updated_at'])
    SubscriptionEvent.objects.create(
        user=user,
        subscription=subscription,
        event_type=SubscriptionEvent.EventType.CANCELED,
        metadata={'actor_id': getattr(actor, 'id', None)},
    )
    return subscription


def subscription_summary_for_user(user):
    subscription = get_or_create_user_subscription(user)
    usage = recalculate_storage_used(user)
    return {
        'plan': subscription.plan,
        'subscription': subscription,
        'usage': usage,
        'limits': get_user_limits(user),
        'features': get_user_features(user),
        'remaining': get_remaining_limits(user),
        'effective_limits': {
            'max_file_size_mb': effective_max_file_size_mb(user),
        },
    }

def _usage_operation_key(job=None, idempotency_key=None):
    if idempotency_key:
        return str(idempotency_key)[:128]
    if job is not None:
        return str(getattr(job, 'idempotency_key', '') or getattr(job, 'public_id', ''))[:128]
    raise ValueError('An idempotency key is required for a usage ledger operation.')


@transaction.atomic
def reserve_character_request(user, character, *, job=None, idempotency_key=None, policy_version='usage-v1'):
    """Atomically validate and reserve one AI request.

    The older can_use/consume pair remains for compatibility. New asynchronous AI
    jobs should use this method to prevent concurrent requests from exceeding a plan.
    """
    features = get_user_features(user)
    feature_key = CHARACTER_FEATURE_KEYS.get(character)
    if feature_key and not features.get(feature_key, False):
        raise SubscriptionFeatureNotAllowed(
            'هذه الشخصية غير متاحة في خطتك الحالية.',
            code='character_not_allowed',
            character=character,
        )

    operation_key = _usage_operation_key(job, idempotency_key)
    usage = get_or_create_current_usage(user)
    usage = SubscriptionUsage.objects.select_for_update().get(pk=usage.pk)
    if UsageLedgerEntry.objects.filter(
        user=user,
        operation=UsageLedgerEntry.Operation.RESERVE,
        idempotency_key=operation_key,
    ).exists():
        return usage
    limits = get_user_limits(user)

    general_limit = limit_value(limits, 'max_ai_requests_per_month')
    if general_limit is not None and usage.ai_requests_used >= general_limit:
        _raise_limit(
            'لقد استهلكت الحد الشهري لطلبات الذكاء الاصطناعي.',
            'ai_request_limit_reached',
            general_limit,
            usage.ai_requests_used,
        )

    limit_key = CHARACTER_LIMIT_KEYS.get(character)
    usage_field = CHARACTER_USAGE_FIELDS.get(character)
    if usage_field:
        character_limit = limit_value(limits, limit_key)
        used = getattr(usage, usage_field)
        if character_limit is not None and used >= character_limit:
            _raise_limit(
                f'لقد استهلكت حد استخدام {character} لهذا الشهر.',
                'character_limit_reached',
                character_limit,
                used,
            )
        setattr(usage, usage_field, used + 1)
    usage.ai_requests_used += 1
    update_fields = ['ai_requests_used', 'updated_at']
    if usage_field:
        update_fields.append(usage_field)
    usage.save(update_fields=update_fields)
    UsageLedgerEntry.objects.create(
        user=user,
        subscription=usage.subscription,
        job=job,
        period_start=usage.period_start,
        period_end=usage.period_end,
        task_type=getattr(job, 'task_type', '') or character,
        operation=UsageLedgerEntry.Operation.RESERVE,
        units=1,
        idempotency_key=operation_key,
        policy_version=policy_version,
        actor_type='user',
        metadata={'character': character},
    )
    return usage


@transaction.atomic
def commit_character_request(job, *, policy_version='usage-v1'):
    """Record the single, final commit for a successfully materialized AI job."""

    operation_key = _usage_operation_key(job)
    usage = get_or_create_current_usage(job.user)
    usage = SubscriptionUsage.objects.select_for_update().get(pk=usage.pk)
    _, created = UsageLedgerEntry.objects.get_or_create(
        user=job.user,
        operation=UsageLedgerEntry.Operation.COMMIT,
        idempotency_key=operation_key,
        defaults={
            'subscription': usage.subscription,
            'job': job,
            'period_start': usage.period_start,
            'period_end': usage.period_end,
            'task_type': job.task_type,
            'units': 1,
            'policy_version': policy_version,
            'actor_type': 'system',
            'metadata': {'character': job.character},
        },
    )
    return created


@transaction.atomic
def refund_character_request(user, character, *, job=None, idempotency_key=None, policy_version='usage-v1'):
    """Refund exactly one reservation after a canceled or failed job."""
    operation_key = _usage_operation_key(job, idempotency_key)
    usage = get_or_create_current_usage(user)
    usage = SubscriptionUsage.objects.select_for_update().get(pk=usage.pk)
    if UsageLedgerEntry.objects.filter(
        user=user,
        operation=UsageLedgerEntry.Operation.REFUND,
        idempotency_key=operation_key,
    ).exists():
        return usage
    usage_field = CHARACTER_USAGE_FIELDS.get(character)
    usage.ai_requests_used = max(usage.ai_requests_used - 1, 0)
    update_fields = ['ai_requests_used', 'updated_at']
    if usage_field:
        setattr(usage, usage_field, max(getattr(usage, usage_field) - 1, 0))
        update_fields.append(usage_field)
    usage.save(update_fields=update_fields)
    UsageLedgerEntry.objects.create(
        user=user,
        subscription=usage.subscription,
        job=job,
        period_start=usage.period_start,
        period_end=usage.period_end,
        task_type=getattr(job, 'task_type', '') or character,
        operation=UsageLedgerEntry.Operation.REFUND,
        units=1,
        idempotency_key=operation_key,
        policy_version=policy_version,
        actor_type='system',
        metadata={'character': character},
    )
    return usage


@transaction.atomic
def rebuild_usage_projection(user, *, period_start=None, period_end=None):
    """Rebuild the mutable usage projection from immutable ledger entries."""

    usage = get_or_create_current_usage(user)
    if period_start is not None or period_end is not None:
        usage = SubscriptionUsage.objects.select_for_update().get(
            user=user,
            period_start=period_start or usage.period_start,
            period_end=period_end or usage.period_end,
        )
    else:
        usage = SubscriptionUsage.objects.select_for_update().get(pk=usage.pk)

    entries = UsageLedgerEntry.objects.filter(
        user=user,
        period_start=usage.period_start,
        period_end=usage.period_end,
    )
    reserved = sum(entry.units for entry in entries.filter(operation=UsageLedgerEntry.Operation.RESERVE))
    refunded = sum(entry.units for entry in entries.filter(operation=UsageLedgerEntry.Operation.REFUND))
    usage.ai_requests_used = max(reserved - refunded, 0)
    for character, field in CHARACTER_USAGE_FIELDS.items():
        character_entries = entries.filter(metadata__character=character)
        character_reserved = sum(
            entry.units for entry in character_entries.filter(operation=UsageLedgerEntry.Operation.RESERVE)
        )
        character_refunded = sum(
            entry.units for entry in character_entries.filter(operation=UsageLedgerEntry.Operation.REFUND)
        )
        setattr(usage, field, max(character_reserved - character_refunded, 0))
    usage.save(update_fields=['ai_requests_used', *CHARACTER_USAGE_FIELDS.values(), 'updated_at'])
    return usage
