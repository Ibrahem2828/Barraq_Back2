from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class SupportTicket(BaseModel):
    class Category(models.TextChoices):
        TECHNICAL = "technical", "Technical"
        ACCOUNT = "account", "Account"
        BILLING = "billing", "Billing"
        CONTENT = "content", "Content"
        AI_RESULT = "ai_result", "AI result"
        OTHER = "other", "Other"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        WAITING_USER = "waiting_user", "Waiting for user"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_tickets")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="assigned_support_tickets", null=True, blank=True)
    subject = models.CharField(max_length=255)
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.OTHER, db_index=True)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM, db_index=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.OPEN, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [
            models.Index(fields=("status", "priority", "-created_at"), name="support_status_priority_idx"),
            models.Index(fields=("user", "-created_at"), name="support_user_date_idx"),
        ]


class SupportMessage(BaseModel):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_messages")
    body = models.TextField()
    is_internal = models.BooleanField(default=False)

    class Meta:
        ordering = ("created_at",)
