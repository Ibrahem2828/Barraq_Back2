from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail


@shared_task(bind=True, max_retries=3, retry_backoff=True, retry_jitter=True, name="users.send_password_reset_email")
def send_password_reset_email(self, email: str, reset_url: str):
    try:
        return send_mail(
            "إعادة تعيين كلمة مرور برّاق",
            f"استخدم الرابط التالي لإعادة تعيين كلمة المرور: {reset_url}",
            settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=False,
        )
    except Exception as exc:  # noqa: BLE001 -- any send failure should retry, not crash the worker
        raise self.retry(exc=exc) from exc
