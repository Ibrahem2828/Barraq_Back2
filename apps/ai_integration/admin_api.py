from django.db.models import Avg, Count
from rest_framework import filters, mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.admin_dashboard.permissions import HasAdminPermission, IsAdminDashboardUser

from .models import AIFeedback, AIJob, AIWebhookEvent
from .serializers import AIFeedbackSerializer, AIJobSerializer
from .services import cancel_job


class AdminAIJobViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = "ai_jobs.view"
    serializer_class = AIJobSerializer
    lookup_field = "public_id"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["public_id", "external_job_id", "user__email", "error_message"]
    ordering_fields = ["created_at", "completed_at", "status"]
    ordering = ["-created_at"]

    def get_required_permission(self):
        return "ai_jobs.cancel" if self.action == "cancel" else self.required_permission

    def get_queryset(self):
        queryset = AIJob.objects.select_related("user", "source", "collection", "subject")
        for field in ("status", "character", "task_type"):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        user_id = self.request.query_params.get("user")
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        return queryset

    @action(detail=True, methods=["post"])
    def cancel(self, request, public_id=None):
        return Response(self.get_serializer(cancel_job(self.get_object())).data)

    @action(detail=False, methods=["get"], url_path="metrics")
    def metrics(self, request):
        queryset = self.get_queryset()
        by_status = list(queryset.values("status").annotate(count=Count("id")).order_by("status"))
        by_character = list(queryset.values("character").annotate(count=Count("id")).order_by("character"))
        feedback = AIFeedback.objects.aggregate(count=Count("id"), average_rating=Avg("rating"))
        return Response(
            {
                "total": queryset.count(),
                "by_status": by_status,
                "by_character": by_character,
                "feedback_count": feedback["count"],
                "average_rating": feedback["average_rating"],
            }
        )


class AdminAIFeedbackViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = "ai_feedback.view"
    serializer_class = AIFeedbackSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["user__email", "job__public_id", "comment"]
    ordering_fields = ["created_at", "rating"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = AIFeedback.objects.select_related("user", "job")
        rating = self.request.query_params.get("rating")
        if rating:
            queryset = queryset.filter(rating=rating)
        training_consent = self.request.query_params.get("training_consent")
        if training_consent in {"true", "false"}:
            queryset = queryset.filter(training_consent=training_consent == "true")
        return queryset


class AIWebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIWebhookEvent
        fields = (
            "id",
            "event_id",
            "event_type",
            "external_job_id",
            "payload_hash",
            "processed",
            "error_message",
            "received_at",
            "processed_at",
        )
        read_only_fields = fields


class AdminAIWebhookEventViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = "system.view"
    serializer_class = AIWebhookEventSerializer
    queryset = AIWebhookEvent.objects.all()
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["event_id", "external_job_id", "error_message"]
    ordering_fields = ["received_at", "processed_at"]
    ordering = ["-received_at"]
