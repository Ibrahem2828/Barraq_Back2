from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.common.models import BaseModel


class AIJob(BaseModel):
    class Character(models.TextChoices):
        FAHES = "fahes", "فاحص"
        KHOTA = "khota", "خطى"
        RASHEED = "rasheed", "رشيد"
        KHOLASA = "kholasa", "خلاصة"
        SADA = "sada", "صدى"

    class TaskType(models.TextChoices):
        FAHES_GENERATE_QUIZ = "fahes_generate_quiz", "Generate quiz"
        KHOTA_GENERATE_PLAN = "khota_generate_plan", "Generate study plan"
        RASHEED_RECOMMENDATIONS = "rasheed_recommendations", "Performance recommendations"
        KHOLASA_GENERATE_SUMMARY = "kholasa_generate_summary", "Summarize source"
        SADA_TRANSCRIBE_AUDIO = "sada_transcribe_audio", "Transcribe audio"

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        QUEUED = "queued", "Queued"
        SUBMITTED = "submitted", "Submitted to AI service"
        PROCESSING = "processing", "Processing"
        VALIDATING = "validating", "Validating"
        OUTPUT_READY = "output_ready", "Output ready"
        MATERIALIZING = "materializing", "Materializing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_jobs",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_jobs",
    )
    character = models.CharField(max_length=20, choices=Character.choices, db_index=True)
    task_type = models.CharField(max_length=50, choices=TaskType.choices, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True)
    source = models.ForeignKey(
        "sources.StudentSource",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_jobs",
    )
    collection = models.ForeignKey(
        "sources.StudentSourceCollection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_jobs",
    )
    subject = models.ForeignKey(
        "subjects.Subject",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_jobs",
    )
    external_job_id = models.CharField(max_length=128, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=128, db_index=True)
    contract_version = models.CharField(max_length=20, default="2.0", db_index=True)
    request_id = models.CharField(max_length=128, blank=True, db_index=True)
    input_payload = models.JSONField(default=dict, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    result_type = models.CharField(max_length=40, blank=True)
    result_id = models.CharField(max_length=64, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    credits_reserved = models.BooleanField(default=False)
    credits_committed = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    service_metadata = models.JSONField(default=dict, blank=True)
    quality_metrics = models.JSONField(default=dict, blank=True)
    # The AI service sends this as a list of flag objects (possibly empty),
    # never a mapping -- see app/models/ai_job.py:AIOutput.security_flags in
    # the AI service.
    security_flags = models.JSONField(default=list, blank=True)
    output_schema_version = models.CharField(max_length=20, blank=True)
    # Denormalized from the completion webhook's metadata.usage/.provider so
    # the admin usage dashboard can SUM/GROUP BY in SQL instead of scanning
    # service_metadata JSON on every request.
    provider_account = models.CharField(max_length=40, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=40, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user", "idempotency_key"),
                name="unique_ai_job_idempotency_per_user",
            ),
            models.CheckConstraint(
                condition=Q(source__isnull=True) | Q(collection__isnull=True),
                name="ai_job_single_source_target",
            ),
            models.CheckConstraint(
                condition=(~Q(status="completed")) | (Q(result_type__gt="") & Q(result_id__gt="")),
                name="ai_job_completed_has_materialized_result",
            ),
        ]
        indexes = [
            models.Index(fields=("user", "status", "-created_at"), name="ai_job_user_status_idx"),
            models.Index(fields=("task_type", "status", "-created_at"), name="ai_job_task_status_idx"),
            models.Index(fields=("project", "status", "-created_at"), name="ai_job_project_status_idx"),
        ]

    def __str__(self):
        return f"{self.public_id} - {self.task_type} - {self.status}"


class AIFeedback(BaseModel):
    class FeedbackType(models.TextChoices):
        GENERAL = "general", "General"
        INCORRECT = "incorrect", "Incorrect"
        NOT_GROUNDED = "not_grounded", "Not grounded"
        UNCLEAR = "unclear", "Unclear"
        TOO_EASY = "too_easy", "Too easy"
        TOO_HARD = "too_hard", "Too hard"
        TOO_LONG = "too_long", "Too long"
        TOO_SHORT = "too_short", "Too short"
        OTHER = "other", "Other"

    job = models.ForeignKey(AIJob, on_delete=models.CASCADE, related_name="feedback")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_feedback")
    rating = models.PositiveSmallIntegerField()
    is_helpful = models.BooleanField(null=True, blank=True)
    feedback_type = models.CharField(max_length=30, choices=FeedbackType.choices, default=FeedbackType.GENERAL)
    reason_codes = models.JSONField(default=list, blank=True)
    comment = models.TextField(blank=True)
    corrected_output = models.JSONField(default=dict, blank=True)
    training_consent = models.BooleanField(default=False)
    consent_version = models.CharField(max_length=40, blank=True)
    forwarded_to_ai_service = models.BooleanField(default=False)
    # Captured from AIJob.service_metadata['telemetry'] at feedback-creation
    # time -- never client-supplied (spec: "provider/model/prompt_version
    # snapshot تُلتقط من Job عند التقييم؛ لا يحددها المستخدم").
    provider_snapshot = models.CharField(max_length=40, blank=True)
    model_snapshot = models.CharField(max_length=100, blank=True)
    prompt_version_snapshot = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=("job", "user"), name="unique_feedback_per_ai_job_user"),
            models.CheckConstraint(condition=Q(rating__gte=1) & Q(rating__lte=5), name="ai_feedback_rating_1_5"),
        ]


class AIWebhookEvent(models.Model):
    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=80)
    external_job_id = models.CharField(max_length=128, blank=True, db_index=True)
    payload_hash = models.CharField(max_length=64)
    processed = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-received_at",)


class AIJobDispatchOutbox(BaseModel):
    """Durable dispatch record. The Celery message that follows
    ``transaction.on_commit`` is a latency optimization only -- this row is
    the source of truth for whether a job has actually been sent to the AI
    service, so a Beat-driven sweep can recover a job that a broker/worker
    gap would otherwise lose forever (spec: 'Accepted job loss: 0')."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DISPATCHING = "dispatching", "Dispatching"
        DISPATCHED = "dispatched", "Dispatched"
        FAILED = "failed", "Failed"

    job = models.OneToOneField(AIJob, on_delete=models.CASCADE, related_name="dispatch_outbox")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(default=timezone.now, db_index=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("next_retry_at",)
        indexes = [
            models.Index(fields=("status", "next_retry_at"), name="ai_outbox_status_retry_idx"),
        ]

    def __str__(self):
        return f"outbox[{self.job_id}] {self.status}"
