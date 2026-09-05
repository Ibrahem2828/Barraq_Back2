from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class Transcription(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transcriptions")
    source = models.ForeignKey("sources.StudentSource", on_delete=models.SET_NULL, null=True, blank=True, related_name="transcriptions")
    ai_job = models.OneToOneField("ai_integration.AIJob", on_delete=models.SET_NULL, null=True, blank=True, related_name="transcription")
    title = models.CharField(max_length=255)
    language = models.CharField(max_length=20, default="ar")
    full_transcript = models.TextField()
    cleaned_transcript = models.TextField(blank=True)
    segments = models.JSONField(default=list, blank=True)
    detected_topics = models.JSONField(default=list, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    confidence_score = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("user", "-created_at"), name="transcript_user_date_idx")]
