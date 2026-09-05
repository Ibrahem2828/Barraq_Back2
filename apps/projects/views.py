from django.db.models import Count
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Project
from .serializers import ProjectActivitySerializer, ProjectCreateUpdateSerializer, ProjectSerializer
from .services import record_project_activity


class ProjectViewSet(viewsets.ModelViewSet):
    """Owner-scoped project workspace API; no client supplied owner is accepted."""

    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "public_id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return (
            Project.objects.filter(owner=self.request.user)
            .select_related("subject")
            .annotate(source_count=Count("sources", distinct=True), ai_job_count=Count("ai_jobs", distinct=True))
        )

    def get_serializer_class(self):
        return ProjectCreateUpdateSerializer if self.action in {"create", "partial_update"} else ProjectSerializer

    def perform_create(self, serializer):
        project = serializer.save(owner=self.request.user)
        record_project_activity(project=project, event_type="project.created", actor=self.request.user, artifact=project)

    def perform_update(self, serializer):
        project = serializer.save()
        record_project_activity(project=project, event_type="project.updated", actor=self.request.user, artifact=project)

    def perform_destroy(self, instance):
        instance.delete()
        record_project_activity(project=instance, event_type="project.deleted", actor=self.request.user, artifact=instance)

    @action(detail=True, methods=["post"])
    def archive(self, request, public_id=None):
        project = self.get_object()
        project.status = Project.Status.ARCHIVED
        project.save(update_fields=["status", "updated_at"])
        record_project_activity(project=project, event_type="project.archived", actor=request.user, artifact=project)
        return Response(ProjectSerializer(project).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, public_id=None):
        project = self.get_object()
        project.status = Project.Status.ACTIVE
        project.save(update_fields=["status", "updated_at"])
        record_project_activity(project=project, event_type="project.restored", actor=request.user, artifact=project)
        return Response(ProjectSerializer(project).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"])
    def activity(self, request, public_id=None):
        project = self.get_object()
        page = self.paginate_queryset(project.activities.select_related("actor"))
        serializer = ProjectActivitySerializer(page or project.activities.select_related("actor"), many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)
