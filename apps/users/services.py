"""Registration, OTP and account-lifecycle operations.

New registration data is held only in ``PendingRegistration``. The permanent
``User`` and its profile are created together only after the current OTP has
been verified while the pending row is locked.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from .exceptions import RegistrationConflict, RegistrationValidationError
from .identity import normalize_email
from .models import EmailOTP, PendingRegistration, User

OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_SENDS_PER_WINDOW = 5
OTP_SEND_WINDOW_MINUTES = 60
PENDING_REGISTRATION_RETENTION_HOURS = 24


@dataclass(frozen=True)
class PendingOtpDispatch:
    normalized_email: str
    code: str | None
    expires_in_seconds: int
    resend_after_seconds: int


def _setting_int(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def _otp_ttl() -> timedelta:
    return timedelta(minutes=_setting_int('OTP_TTL_MINUTES', OTP_TTL_MINUTES))


def _otp_attempt_limit() -> int:
    return _setting_int('OTP_MAX_ATTEMPTS', OTP_MAX_ATTEMPTS)


def _resend_cooldown() -> int:
    return _setting_int('OTP_RESEND_COOLDOWN_SECONDS', OTP_RESEND_COOLDOWN_SECONDS)


def _send_window() -> timedelta:
    return timedelta(minutes=_setting_int('OTP_SEND_WINDOW_MINUTES', OTP_SEND_WINDOW_MINUTES))


def _send_limit() -> int:
    return _setting_int('OTP_MAX_SENDS_PER_WINDOW', OTP_MAX_SENDS_PER_WINDOW)


def _new_code() -> str:
    return f'{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}'


def _seconds_until(value, now) -> int:
    return max(0, int((value - now).total_seconds()))


def pending_resend_cooldown_remaining_seconds(pending: PendingRegistration, *, now=None) -> int:
    now = now or timezone.now()
    return max(0, _resend_cooldown() - int((now - pending.last_otp_sent_at).total_seconds()))


def _ensure_send_budget(pending: PendingRegistration, *, now) -> None:
    """Apply the per-email resend ceiling under the pending-row lock."""

    if now - pending.otp_send_window_started_at >= _send_window():
        pending.otp_send_window_started_at = now
        pending.otp_send_count = 0
    if pending.otp_send_count >= _send_limit():
        retry_after = _seconds_until(pending.otp_send_window_started_at + _send_window(), now)
        raise RegistrationValidationError(
            {'retry_after_seconds': [str(retry_after)]},
            code='otp_resend_limit_exceeded',
        )


def _issue_pending_otp(pending: PendingRegistration, *, now) -> PendingOtpDispatch:
    """Replace the one active code and deliberately reset its attempts.

    The prior hash is overwritten, so an old code cannot be replayed. Attempt
    reset is safe because issuing a new code is itself protected by a per-email
    send window and a server-side cooldown.
    """

    _ensure_send_budget(pending, now=now)
    code = _new_code()
    pending.otp_hash = make_password(code)
    pending.otp_expires_at = now + _otp_ttl()
    pending.otp_attempt_count = 0
    pending.otp_send_count += 1
    pending.last_otp_sent_at = now
    pending.save(
        update_fields=[
            'otp_hash',
            'otp_expires_at',
            'otp_attempt_count',
            'otp_send_count',
            'otp_send_window_started_at',
            'last_otp_sent_at',
            'updated_at',
        ]
    )
    return PendingOtpDispatch(
        normalized_email=pending.normalized_email,
        code=code,
        expires_in_seconds=int(_otp_ttl().total_seconds()),
        resend_after_seconds=_resend_cooldown(),
    )


def _create_pending_registration(*, email: str, full_name: str, phone_number: str, password: str, now):
    code = _new_code()
    pending = PendingRegistration.objects.create(
        normalized_email=email,
        full_name=full_name,
        phone_number=phone_number,
        password_hash=make_password(password),
        otp_hash=make_password(code),
        otp_expires_at=now + _otp_ttl(),
        otp_send_count=1,
        otp_send_window_started_at=now,
        last_otp_sent_at=now,
    )
    return pending, PendingOtpDispatch(
        normalized_email=pending.normalized_email,
        code=code,
        expires_in_seconds=int(_otp_ttl().total_seconds()),
        resend_after_seconds=_resend_cooldown(),
    )


def start_pending_registration(*, email: str, full_name: str, password: str, phone_number: str = '') -> PendingOtpDispatch:
    """Start or safely continue a registration without creating a ``User``.

    A live pending registration is immutable through duplicate register posts:
    they may request a replacement OTP after the cooldown but cannot overwrite
    the password or profile data that the email owner is about to verify. An
    expired OTP starts a fresh pending attempt using the newly validated form.
    """

    normalized_email = normalize_email(email)
    now = timezone.now()
    with transaction.atomic():
        if User.objects.filter(email__iexact=normalized_email).exists():
            raise RegistrationValidationError(
                {'email': ['A user with this email already exists.']},
                code='email_already_registered',
            )

        pending = PendingRegistration.objects.select_for_update().filter(
            normalized_email__iexact=normalized_email,
        ).first()
        if pending is None:
            # The unique constraint is the final arbiter when two first-time
            # posts race. An inner savepoint lets the outer transaction keep
            # running after the losing insert sees IntegrityError.
            try:
                with transaction.atomic():
                    _, dispatch = _create_pending_registration(
                        email=normalized_email,
                        full_name=full_name,
                        phone_number=phone_number,
                        password=password,
                        now=now,
                    )
                    return dispatch
            except IntegrityError:
                pending = PendingRegistration.objects.select_for_update().get(
                    normalized_email__iexact=normalized_email,
                )

        if pending.otp_expires_at <= now:
            # No valid code remains, so a new form submission deliberately
            # replaces the pending credentials/profile before issuing a code.
            pending.full_name = full_name
            pending.phone_number = phone_number
            pending.password_hash = make_password(password)
            pending.save(update_fields=['full_name', 'phone_number', 'password_hash', 'updated_at'])
            return _issue_pending_otp(pending, now=now)

        cooldown = pending_resend_cooldown_remaining_seconds(pending, now=now)
        if cooldown:
            return PendingOtpDispatch(
                normalized_email=pending.normalized_email,
                code=None,
                expires_in_seconds=_seconds_until(pending.otp_expires_at, now),
                resend_after_seconds=cooldown,
            )
        return _issue_pending_otp(pending, now=now)


def resend_pending_registration(email: str) -> PendingOtpDispatch | None:
    """Issue a new OTP for a pending registration, without account disclosure."""

    normalized_email = normalize_email(email)
    now = timezone.now()
    with transaction.atomic():
        pending = PendingRegistration.objects.select_for_update().filter(
            normalized_email__iexact=normalized_email,
        ).first()
        if pending is None:
            return None
        cooldown = pending_resend_cooldown_remaining_seconds(pending, now=now)
        if cooldown:
            raise RegistrationValidationError(
                {'retry_after_seconds': [str(cooldown)]},
                code='otp_resend_cooldown',
            )
        return _issue_pending_otp(pending, now=now)


def verify_pending_registration(email: str, code: str) -> User:
    """Atomically consume a pending OTP and create exactly one permanent user."""

    normalized_email = normalize_email(email)
    now = timezone.now()
    invalid_attempt_code: str | None = None
    try:
        with transaction.atomic():
            pending = PendingRegistration.objects.select_for_update().filter(
                normalized_email__iexact=normalized_email,
            ).first()
            if pending is None:
                raise RegistrationValidationError(
                    {'email': ['No pending registration exists for this email.']},
                    code='pending_registration_missing',
                )
            if pending.otp_expires_at <= now:
                raise RegistrationValidationError({'code': ['The OTP has expired.']}, code='otp_expired')
            if pending.otp_attempt_count >= _otp_attempt_limit():
                raise RegistrationValidationError(
                    {'code': ['Too many OTP attempts.']},
                    code='otp_too_many_attempts',
                )
            if not check_password(code, pending.otp_hash):
                pending.otp_attempt_count += 1
                pending.save(update_fields=['otp_attempt_count', 'updated_at'])
                invalid_attempt_code = (
                    'otp_too_many_attempts'
                    if pending.otp_attempt_count >= _otp_attempt_limit()
                    else 'otp_invalid'
                )
            else:
                if User.objects.filter(email__iexact=normalized_email).exists():
                    raise RegistrationConflict()

                user = User(
                    email=pending.normalized_email,
                    full_name=pending.full_name,
                    phone_number=pending.phone_number,
                    role=User.Roles.STUDENT,
                    is_active=True,
                    is_verified=True,
                    password=pending.password_hash,
                )
                user.save()

                from apps.students.models import StudentProfile

                StudentProfile.objects.get_or_create(user=user)
                pending.delete()
                return user
    except IntegrityError as exc:
        # A concurrent external account creation must not leak a database
        # exception or leave a partially created user/profile behind.
        raise RegistrationConflict() from exc

    # Raise after the transaction has committed the increment. Raising inside
    # ``atomic`` would roll back the very brute-force counter we need to keep.
    if invalid_attempt_code:
        raise RegistrationValidationError({'code': ['The OTP is invalid.']}, code=invalid_attempt_code)
    raise RegistrationConflict()


def cleanup_expired_pending_registrations(*, now=None) -> int:
    """Delete stale temporary registration data, never permanent users."""

    now = now or timezone.now()
    retention = timedelta(
        hours=_setting_int('PENDING_REGISTRATION_RETENTION_HOURS', PENDING_REGISTRATION_RETENTION_HOURS)
    )
    deleted, _ = PendingRegistration.objects.filter(updated_at__lt=now - retention).delete()
    return deleted


# -- Legacy compatibility -------------------------------------------------
# These functions are intentionally limited to historic User(is_verified=False)
# rows created before PendingRegistration existed. New registrations do not
# call them. They keep the rollout non-destructive for currently pending
# learners while the permanent-account-before-OTP architecture is retired.

def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode('utf-8')).hexdigest()


def issue_email_otp(user: User) -> str:
    code = _new_code()
    EmailOTP.objects.create(
        user=user,
        code_hash=_hash_code(code),
        expires_at=timezone.now() + _otp_ttl(),
    )
    return code


def latest_pending_otp(user: User) -> EmailOTP | None:
    return EmailOTP.objects.filter(user=user, consumed_at__isnull=True).order_by('-created_at').first()


def verify_email_otp(user: User, code: str) -> str:
    """Safely verify a legacy OTP without issuing a session for an existing user."""

    with transaction.atomic():
        locked_user = User.objects.select_for_update().filter(pk=user.pk, is_active=True).first()
        if locked_user is None:
            return 'invalid'
        if locked_user.is_verified:
            return 'already_verified'
        otp = EmailOTP.objects.select_for_update().filter(
            user=locked_user,
            consumed_at__isnull=True,
        ).order_by('-created_at').first()
        if otp is None:
            return 'invalid'
        if otp.attempts >= _otp_attempt_limit():
            return 'too_many_attempts'
        if otp.expires_at <= timezone.now():
            return 'expired'
        if not hmac.compare_digest(otp.code_hash, _hash_code(code)):
            otp.attempts += 1
            otp.save(update_fields=['attempts', 'updated_at'])
            return 'too_many_attempts' if otp.attempts >= _otp_attempt_limit() else 'invalid'

        otp.consumed_at = timezone.now()
        otp.save(update_fields=['consumed_at', 'updated_at'])
        locked_user.is_verified = True
        locked_user.save(update_fields=['is_verified', 'updated_at'])
        return 'ok'


def resend_cooldown_remaining_seconds(user: User) -> int:
    otp = latest_pending_otp(user)
    if otp is None:
        return 0
    return max(0, _resend_cooldown() - int((timezone.now() - otp.created_at).total_seconds()))


def delete_user_account(user: User) -> None:
    """Soft-delete, anonymize and revoke every session for a user account."""

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
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    outstanding = OutstandingToken.objects.filter(user_id=user.pk)
    BlacklistedToken.objects.bulk_create(
        (BlacklistedToken(token=token) for token in outstanding),
        ignore_conflicts=True,
    )
