from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel


class Notification(BaseModel):
    class Category(models.TextChoices):
        SYSTEM = "system", "System"
        STUDY = "study", "Study"
        QUIZ = "quiz", "Quiz"
        PLAN = "plan", "Plan"
        AI = "ai", "AI"
        SUBSCRIPTION = "subscription", "Subscription"
        SUPPORT = "support", "Support"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.SYSTEM, db_index=True)
    title = models.CharField(max_length=255)
    body = models.TextField()
    data = models.JSONField(default=dict, blank=True)
    action_url = models.CharField(max_length=500, blank=True)
    idempotency_key = models.CharField(max_length=160, blank=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("user", "read_at", "-created_at"), name="notif_user_read_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("user", "idempotency_key"),
                condition=~Q(idempotency_key=""),
                name="unique_notification_idempotency_key",
            ),
        ]

    @property
    def is_read(self):
        return self.read_at is not None
