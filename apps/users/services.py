import hashlib
import hmac
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import EmailOTP, User

OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode('utf-8')).hexdigest()


def issue_email_otp(user: User) -> str:
    """Generates a fresh 6-digit code, stores only its hash, and returns the
    plaintext code for the caller to email. Does not invalidate prior codes
    for this user -- `verify_email_otp` only ever considers the newest
    unconsumed one, so an older code simply stops being reachable."""

    code = f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"
    EmailOTP.objects.create(
        user=user,
        code_hash=_hash_code(code),
        expires_at=timezone.now() + timedelta(minutes=OTP_TTL_MINUTES),
    )
    return code


def latest_pending_otp(user: User) -> EmailOTP | None:
    return EmailOTP.objects.filter(user=user, consumed_at__isnull=True).order_by('-created_at').first()


def verify_email_otp(user: User, code: str) -> str:
    """Returns one of: "ok", "invalid", "expired", "too_many_attempts",
    "already_verified". On "ok", marks the OTP consumed and the user
    verified in the same transaction."""

    if user.is_verified:
        return 'already_verified'

    otp = latest_pending_otp(user)
    if otp is None:
        return 'invalid'
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        return 'too_many_attempts'
    if otp.expires_at < timezone.now():
        return 'expired'

    if not hmac.compare_digest(otp.code_hash, _hash_code(code)):
        otp.attempts += 1
        otp.save(update_fields=['attempts', 'updated_at'])
        return 'too_many_attempts' if otp.attempts >= OTP_MAX_ATTEMPTS else 'invalid'

    with transaction.atomic():
        otp.consumed_at = timezone.now()
        otp.save(update_fields=['consumed_at', 'updated_at'])
        user.is_verified = True
        user.save(update_fields=['is_verified', 'updated_at'])
    return 'ok'


def resend_cooldown_remaining_seconds(user: User) -> int:
    """0 if a new OTP may be issued now, otherwise how many seconds until it may."""

    otp = latest_pending_otp(user)
    if otp is None:
        return 0
    elapsed = (timezone.now() - otp.created_at).total_seconds()
    remaining = OTP_RESEND_COOLDOWN_SECONDS - elapsed
    return max(0, int(remaining))


def delete_user_account(user: User) -> None:
    """Self-service account deletion. Soft-deletes the row (the existing
    SoftDeleteModel architecture -- related data such as projects/sources/
    quiz history is intentionally left in place for legal/audit purposes,
    but becomes unreachable once the owning account can no longer
    authenticate), anonymizes personally-identifying fields, and revokes
    every outstanding refresh token so no existing session survives.

    Deterministic and idempotent: the anonymized email is derived from the
    user's own primary key (not a random value), so calling this twice for
    the same user is a no-op the second time -- it never raises a unique
    constraint error and never re-sends any notification.
    """

    anonymized_email = f"deleted-user-{user.pk}@deleted.baraq.invalid"
    with transaction.atomic():
        User.all_objects.filter(pk=user.pk).update(
            email=anonymized_email,
            full_name="",
            phone_number="",
            is_active=False,
            is_deleted=True,
            deleted_at=timezone.now(),
        )
        _revoke_all_tokens(user)


def _revoke_all_tokens(user: User) -> None:
    """Blacklists every refresh token ever issued to this user. Access tokens
    are not separately revocable (they are stateless JWTs), but that is safe
    here: SimpleJWT's ``JWTAuthentication.get_user`` re-fetches the user via
    ``User.objects`` (the soft-delete-aware default manager) on every
    authenticated request and rejects it once ``is_deleted``/``is_active``
    flip, so an already-issued access token stops working immediately too.
    """

    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    outstanding = OutstandingToken.objects.filter(user_id=user.pk)
    BlacklistedToken.objects.bulk_create(
        (BlacklistedToken(token=token) for token in outstanding),
        ignore_conflicts=True,
    )
