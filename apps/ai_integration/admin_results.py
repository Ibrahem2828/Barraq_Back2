from collections.abc import Sequence

from rest_framework import filters, mixins, viewsets
from rest_framework.filters import BaseFilterBackend
from rest_framework.permissions import BasePermission

from apps.admin_dashboard.permissions import HasAdminPermission, IsAdminDashboardUser
from apps.analytics.models import StudentRecommendation
from apps.analytics.serializers import StudentRecommendationSerializer
from apps.audio.models import Transcription
from apps.audio.serializers import TranscriptionSerializer
from apps.summaries.models import Summary
from apps.summaries.serializers import SummarySerializer


# Every subclass combining this mixin with a GenericAPIView/APIView needs a
# `# type: ignore[misc]` on its class line: mypy's "conflicting definition
# across unrelated bases" check considers the mixin and the DRF base class
# unrelated (the mixin itself doesn't inherit APIView), so it flags the
# override even though the annotated types below are actually compatible.
# This is a standard Django/DRF composition pattern, not a real conflict.
class AdminAIResultMixin:
    permission_classes: Sequence[type[BasePermission]] = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = "ai_jobs.view"
    filter_backends: Sequence[type[BaseFilterBackend]] = [filters.SearchFilter, filters.OrderingFilter]
    ordering = ["-created_at"]


class AdminRecommendationViewSet(AdminAIResultMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):  # type: ignore[misc]
    serializer_class = StudentRecommendationSerializer
    queryset = StudentRecommendation.objects.select_related("user", "subject", "ai_job")
    search_fields = ["title", "summary", "user__email"]
    ordering_fields = ["created_at", "overall_score"]


class AdminSummaryViewSet(AdminAIResultMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):  # type: ignore[misc]
    serializer_class = SummarySerializer
    queryset = Summary.objects.select_related("user", "source", "collection", "ai_job")
    search_fields = ["title", "short_summary", "user__email"]
    ordering_fields = ["created_at", "quality_score"]


class AdminTranscriptionViewSet(AdminAIResultMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):  # type: ignore[misc]
    serializer_class = TranscriptionSerializer
    queryset = Transcription.objects.select_related("user", "source", "ai_job")
    search_fields = ["title", "full_transcript", "user__email"]
    ordering_fields = ["created_at", "duration_seconds", "confidence_score"]
