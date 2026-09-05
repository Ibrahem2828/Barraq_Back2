from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class StudentRecommendation(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recommendations")
    subject = models.ForeignKey("subjects.Subject", on_delete=models.SET_NULL, null=True, blank=True, related_name="recommendations")
    ai_job = models.OneToOneField("ai_integration.AIJob", on_delete=models.SET_NULL, null=True, blank=True, related_name="recommendation")
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    strengths = models.JSONField(default=list, blank=True)
    weaknesses = models.JSONField(default=list, blank=True)
    recommendations = models.JSONField(default=list, blank=True)
    next_best_action = models.JSONField(default=dict, blank=True)
    source_metrics = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("user", "-created_at"), name="recommend_user_date_idx")]
