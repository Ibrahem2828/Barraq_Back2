import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_subscription_for_new_user(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from .services import get_or_create_user_subscription

        get_or_create_user_subscription(instance)
    except Exception:
        logger.exception('Failed to create default subscription for new user.')
