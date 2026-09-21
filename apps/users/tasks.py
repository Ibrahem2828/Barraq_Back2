import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from .services import cleanup_expired_pending_registrations

logger = logging.getLogger(__name__)


def _dispatch(subject, body, recipient, *, kind):
    """Hand one message to the SMTP backend.

    ``send_mail`` returns the number of messages it accepted. Zero means the
    backend took nothing, which is a delivery failure that previously looked
    exactly like success -- the task returned 0 and Celery marked it done,
    so a student waited for a code that was never sent. Raising makes the
    caller retry.

    Nothing identifying is logged: no code, no recipient address, no SMTP
    credentials. The task id is enough to correlate with the worker log.
    """

    accepted = send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
    if not accepted:
        raise OSError(f'The mail backend accepted no {kind} message.')
    logger.info('%s_email_handed_to_smtp', kind)
    return accepted


@shared_task(bind=True, max_retries=3, retry_backoff=True, retry_jitter=True, name="users.send_password_reset_email")
def send_password_reset_email(self, email: str, reset_url: str):
    try:
        return _dispatch(
            "إعادة تعيين كلمة مرور برّاق",
            f"استخدم الرابط التالي لإعادة تعيين كلمة المرور: {reset_url}",
            email,
            kind="password_reset",
        )
    except Exception as exc:  # noqa: BLE001 -- any send failure should retry, not crash the worker
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, retry_backoff=True, retry_jitter=True, name="users.send_email_otp")
def send_email_otp(self, email: str, code: str):
    try:
        return _dispatch(
            "رمز تحقق برّاق",
            f"رمز التحقق الخاص بك هو: {code}\nصالح لمدة {settings.OTP_TTL_MINUTES} دقائق ولا تشاركه مع أحد.",
            email,
            kind="otp",
        )
    except Exception as exc:  # noqa: BLE001 -- any send failure should retry, not crash the worker
        raise self.retry(exc=exc) from exc


@shared_task(name="users.cleanup_expired_pending_registrations")
def cleanup_expired_pending_registrations_task():
    """Remove only stale temporary registration data on the normal queue."""

    deleted = cleanup_expired_pending_registrations()
    logger.info('expired_pending_registrations_cleaned', extra={'deleted_count': deleted})
    return deleted
