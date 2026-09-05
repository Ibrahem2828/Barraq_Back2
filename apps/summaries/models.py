from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class Summary(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="summaries")
    source = models.ForeignKey("sources.StudentSource", on_delete=models.SET_NULL, null=True, blank=True, related_name="summaries")
    collection = models.ForeignKey("sources.StudentSourceCollection", on_delete=models.SET_NULL, null=True, blank=True, related_name="summaries")
    ai_job = models.OneToOneField("ai_integration.AIJob", on_delete=models.SET_NULL, null=True, blank=True, related_name="summary")
    title = models.CharField(max_length=255)
    short_summary = models.TextField(blank=True)
    detailed_summary = models.TextField(blank=True)
    key_points = models.JSONField(default=list, blank=True)
    important_terms = models.JSONField(default=list, blank=True)
    covered_topics = models.JSONField(default=list, blank=True)
    review_questions = models.JSONField(default=list, blank=True)
    source_references = models.JSONField(default=list, blank=True)
    quality_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("user", "-created_at"), name="summary_user_date_idx")]
