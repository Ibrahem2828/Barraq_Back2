"""Manual, payment-backed activation of paid subscriptions.

Baraq takes no payments itself. A student pays through an outside channel and
staff with ``subscriptions.activate`` verify it, then record the activation
here. Three rules make that safe:

* the payment reference is globally unique, so one receipt cannot buy two
  activations (a replay of the *same* activation is answered, not repeated);
* the activation, the subscription change and the audit entry commit together
  or not at all -- a paid plan is never granted without its audit trail;
* no other admin path may hand out a paid entitlement, so there is exactly one
  way to give someone a paid plan and it always leaves a payment reference.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from .exceptions import SubscriptionError
from .models import (
    SubscriptionActivation,
    SubscriptionEvent,
    SubscriptionPlan,
    UserSubscription,
)
from .services import get_or_create_user_subscription

ENTITLING_STATUSES = frozenset({UserSubscription.Status.ACTIVE, UserSubscription.Status.TRIALING})
MAX_PERIOD_YEARS = 5
_REFERENCE_PATTERN = re.compile(r'^[\w\-./:# ]{4,120}$')


def is_paid_plan(plan: SubscriptionPlan) -> bool:
    return plan.billing_interval != SubscriptionPlan.BillingInterval.FREE


def normalize_payment_reference(raw: str | None) -> str:
    """The reference as a person would read it: NFKC, one space between words."""
    value = unicodedata.normalize('NFKC', raw or '')
    return re.sub(r'\s+', ' ', value).strip()


def payment_reference_key(reference: str) -> str:
    """Comparison key: case- and whitespace-insensitive, so 'tx 9' == 'TX9'."""
    return re.sub(r'\s+', '', normalize_payment_reference(reference)).casefold()


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2 == 1:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def looks_like_card_number(value: str) -> bool:
    digits = re.sub(r'[\s-]', '', value)
    return digits.isdigit() and 13 <= len(digits) <= 19 and _luhn_valid(digits)


def validate_payment_reference(raw: str | None) -> str:
    """Return the cleaned reference or raise; never lets a card number in.

    A card number in a free-text field would put Baraq inside a PCI scope its
    architecture makes no claim to, so anything that is a valid card number is
    refused outright.
    """
    reference = normalize_payment_reference(raw)
    if not _REFERENCE_PATTERN.match(reference):
        raise SubscriptionError(
            'Payment reference must be 4-120 characters: letters, digits, spaces and - . / : #',
            code='invalid_payment_reference',
        )
    if looks_like_card_number(reference):
        raise SubscriptionError(
            'A card number is not a payment reference. Use the receipt or transfer reference.',
            code='payment_reference_looks_like_card',
        )
    return reference


def add_months(moment: datetime, months: int) -> datetime:
    index = moment.month - 1 + months
    year = moment.year + index // 12
    month = index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _extends(new_value: datetime | None, old_value: datetime | None) -> bool:
    """True when ``new_value`` ends later than ``old_value`` (None = never ends)."""
    if new_value is None:
        return old_value is not None
    return old_value is not None and new_value > old_value


def resolve_period_end(plan, *, now, current_end, still_entitled_on_plan, explicit_end):
    interval = plan.billing_interval
    horizon = add_months(now, MAX_PERIOD_YEARS * 12)

    if interval == SubscriptionPlan.BillingInterval.LIFETIME:
        if explicit_end is not None:
            raise SubscriptionError(
                'A lifetime plan has no period end.', code='period_end_not_allowed'
            )
        return None

    if explicit_end is None:
        if interval == SubscriptionPlan.BillingInterval.CUSTOM:
            raise SubscriptionError(
                'This plan has no fixed length: current_period_end is required.',
                code='period_end_required',
            )
        months = 12 if interval == SubscriptionPlan.BillingInterval.YEARLY else 1
        # A renewal of a plan that is still running continues from its end,
        # so paying early never costs the student the days they have left.
        base = current_end if still_entitled_on_plan and current_end and current_end > now else now
        return add_months(base, months)

    if explicit_end <= now:
        raise SubscriptionError('current_period_end must be in the future.', code='period_end_in_past')
    if explicit_end > horizon:
        raise SubscriptionError(
            f'current_period_end is more than {MAX_PERIOD_YEARS} years away.',
            code='period_end_too_far',
        )
    if still_entitled_on_plan and current_end and explicit_end < current_end:
        raise SubscriptionError(
            'current_period_end would shorten a period the student already has.',
            code='period_end_before_existing',
        )
    return explicit_end


def ensure_paid_entitlement_has_activation(
    subscription: UserSubscription,
    *,
    new_plan: SubscriptionPlan | None = None,
    new_status: str | None = None,
    new_period_end: datetime | None = None,
    period_end_given: bool = False,
    new_trial_ends_at: datetime | None = None,
    trial_end_given: bool = False,
    status_defaults_to_active: bool = False,
) -> None:
    """Refuse any non-activation change that would hand out a paid entitlement.

    Downgrading, cancelling, pausing and editing metadata stay open; only the
    moves that *grant* paid access (new paid plan, becoming active/trialing on
    one, or ending later) must go through ``activate_paid_subscription``.
    """
    target_plan = new_plan or subscription.plan
    if not is_paid_plan(target_plan):
        return
    target_status = new_status or (
        UserSubscription.Status.ACTIVE if (new_plan is not None and status_defaults_to_active) else subscription.status
    )
    if target_status not in ENTITLING_STATUSES:
        return
    grants = (
        target_plan.pk != subscription.plan_id
        or target_status != subscription.status
        or (period_end_given and _extends(new_period_end, subscription.current_period_end))
        or (trial_end_given and _extends(new_trial_ends_at, subscription.trial_ends_at))
    )
    if grants:
        raise SubscriptionError(
            'A paid plan can only be granted by recording a payment: use the '
            'activate-subscription action with a payment reference.',
            code='payment_activation_required',
        )


def _replay_or_conflict(existing: SubscriptionActivation, user, plan) -> tuple[SubscriptionActivation, bool]:
    if existing.user_id == user.pk and existing.plan_id == plan.pk:
        return existing, False
    raise SubscriptionError(
        'This payment reference was already used for a different activation.',
        code='payment_reference_already_used',
        status_code=409,
    )


def activate_paid_subscription(
    user,
    plan: SubscriptionPlan,
    *,
    payment_method: str,
    payment_reference: str,
    actor,
    current_period_end: datetime | None = None,
    note: str = '',
    request=None,
) -> tuple[SubscriptionActivation, bool]:
    """Activate ``plan`` for ``user`` against a verified external payment.

    Returns ``(activation, created)``. Repeating a request with the same
    reference, user and plan returns the original activation with
    ``created=False`` and changes nothing; the same reference for anyone or
    anything else raises a 409.
    """
    from apps.admin_dashboard.services import log_admin_action

    if not is_paid_plan(plan):
        raise SubscriptionError('Only a paid plan needs activating.', code='plan_not_paid')
    if not plan.is_active:
        raise SubscriptionError('This plan is not active.', code='plan_inactive')
    if payment_method not in SubscriptionActivation.PaymentMethod.values:
        raise SubscriptionError('Unknown payment method.', code='invalid_payment_method')
    reference = validate_payment_reference(payment_reference)
    key = payment_reference_key(reference)

    with transaction.atomic():
        existing = SubscriptionActivation.objects.filter(reference_key=key).first()
        if existing is not None:
            return _replay_or_conflict(existing, user, plan)

        # One lock per student serialises concurrent activations for them.
        # No select_related here: PostgreSQL re-checks a locked join against the
        # *old* joined row when a competing transaction has changed plan_id
        # while we waited, and returns nothing (the plan is read afterwards).
        subscription = UserSubscription.objects.select_for_update().get(
            pk=get_or_create_user_subscription(user).pk
        )
        now = timezone.now()
        still_on_plan = (
            subscription.plan_id == plan.pk
            and subscription.status in ENTITLING_STATUSES
            and bool(subscription.current_period_end and subscription.current_period_end > now)
        )
        period_end = resolve_period_end(
            plan,
            now=now,
            current_end=subscription.current_period_end,
            still_entitled_on_plan=still_on_plan,
            explicit_end=current_period_end,
        )
        previous_plan_code = subscription.plan.code
        previous_status = subscription.status

        try:
            with transaction.atomic():
                activation = SubscriptionActivation.objects.create(
                    user=user,
                    subscription=subscription,
                    plan=plan,
                    activated_by=actor if getattr(actor, 'is_authenticated', False) else None,
                    payment_method=payment_method,
                    payment_reference=reference,
                    reference_key=key,
                    period_start=subscription.current_period_start if still_on_plan else now,
                    period_end=period_end,
                    previous_plan_code=previous_plan_code,
                    previous_status=previous_status,
                    note=(note or '').strip()[:500],
                )
        except IntegrityError:
            # Lost a race for the same reference: answer from the winner.
            winner = SubscriptionActivation.objects.get(reference_key=key)
            return _replay_or_conflict(winner, user, plan)

        subscription.plan = plan
        subscription.status = UserSubscription.Status.ACTIVE
        subscription.provider = UserSubscription.Provider.MANUAL
        subscription.auto_renew = False
        subscription.canceled_at = None
        subscription.trial_ends_at = None
        subscription.current_period_end = period_end
        if not still_on_plan:
            subscription.current_period_start = now
        subscription.metadata = {
            **(subscription.metadata or {}),
            'last_activation_id': activation.pk,
            'activation_source': 'manual_external_payment',
        }
        subscription.save()

        SubscriptionEvent.objects.create(
            user=user,
            subscription=subscription,
            event_type=SubscriptionEvent.EventType.PLAN_CHANGED,
            metadata={
                'old_plan': previous_plan_code,
                'new_plan': plan.code,
                'actor_id': getattr(actor, 'id', None),
                'activation_id': activation.pk,
            },
        )
        # strict: an audit that fails to write aborts the whole activation.
        log_admin_action(
            actor,
            'subscription.manually_activated',
            subscription,
            {
                'user_id': user.pk,
                'plan_code': plan.code,
                'activation_id': activation.pk,
                'payment_method': payment_method,
                'payment_reference': reference,
                'period_end': period_end.isoformat() if period_end else None,
                'previous_plan': previous_plan_code,
                'previous_status': previous_status,
            },
            request,
            strict=True,
        )
        return activation, True

