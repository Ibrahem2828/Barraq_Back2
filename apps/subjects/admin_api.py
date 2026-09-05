from rest_framework import filters, viewsets

from apps.admin_dashboard.permissions import HasAdminPermission, IsAdminDashboardUser

from .models import EducationStage, Subject
from .serializers import EducationStageSerializer, SubjectSerializer


class AdminEducationStageViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    serializer_class = EducationStageSerializer
    queryset = EducationStage.objects.all().order_by("order", "id")
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["order", "name", "created_at"]
    ordering = ["order", "id"]

    def get_required_permission(self):
        mapping = {
            "list": "education_stages.view",
            "retrieve": "education_stages.view",
            "create": "education_stages.create",
            "update": "education_stages.update",
            "partial_update": "education_stages.update",
            "destroy": "education_stages.delete",
        }
        return mapping.get(self.action, "education_stages.view")


class AdminSubjectViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    serializer_class = SubjectSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description", "grade_level"]
    ordering_fields = ["name", "created_at", "education_stage__order"]
    ordering = ["education_stage__order", "name"]

    def get_required_permission(self):
        mapping = {
            "list": "subjects.view",
            "retrieve": "subjects.view",
            "create": "subjects.create",
            "update": "subjects.update",
            "partial_update": "subjects.update",
            "destroy": "subjects.delete",
        }
        return mapping.get(self.action, "subjects.view")

    def get_queryset(self):
        queryset = Subject.objects.select_related("education_stage").all()
        education_stage = self.request.query_params.get("education_stage")
        is_active = self.request.query_params.get("is_active")
        if education_stage:
            queryset = queryset.filter(education_stage_id=education_stage)
        if is_active in {"true", "false"}:
            queryset = queryset.filter(is_active=is_active == "true")
        return queryset
