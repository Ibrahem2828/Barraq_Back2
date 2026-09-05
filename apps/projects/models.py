from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel, SoftDeleteModel


class Project(SoftDeleteModel):
    """The single, owned workspace that scopes a learner's study artifacts."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    subject = models.ForeignKey(
        "subjects.Subject",
        on_delete=models.SET_NULL,
        related_name="projects",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    goal = models.TextField(blank=True)
    education_context = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    color = models.CharField(max_length=40, blank=True)
    icon = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ("-updated_at", "-created_at")
        constraints = [
            models.CheckConstraint(condition=~models.Q(title=""), name="project_title_not_blank"),
        ]
        indexes = [
            models.Index(fields=("owner", "status", "-updated_at"), name="project_owner_status_idx"),
            models.Index(fields=("owner", "subject", "-created_at"), name="project_owner_subject_idx"),
        ]

    def __str__(self):
        return f"{self.title} ({self.owner_id})"


class ProjectActivity(BaseModel):
    """Append-only project timeline. Metadata deliberately excludes raw content."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="activities")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="project_activities",
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=80, db_index=True)
    request_id = models.CharField(max_length=128, blank=True, db_index=True)
    artifact_type = models.CharField(max_length=80, blank=True)
    artifact_id = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("project", "-created_at"), name="project_activity_timeline_idx"),
        ]

    def __str__(self):
        return f"{self.project_id}: {self.event_type}"
