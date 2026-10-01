"""HTTP surface of the Classroom Shared Library.

Staff:   /api/v1/admin/class-library/      (library.view / library.manage)
Student: /api/v1/class-library/            (active membership)
"""

from __future__ import annotations

from django.db import transaction
from django.http import FileResponse, Http404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.admin_dashboard.permissions import HasAdminPermission, IsAdminDashboardUser
from apps.admin_dashboard.services import log_admin_action
from apps.sources.validators import validate_student_source_file
from apps.subjects.models import Subject

from . import library
from . import scope as scope_policy
from .models import ClassLibraryItem

CATEGORY_CHOICES = ClassLibraryItem.Category.choices


# -- serializers --------------------------------------------------------------
class LibraryItemSerializer(serializers.ModelSerializer):
    organization: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    classroom_name = serializers.CharField(source="classroom.name", read_only=True, default=None)
    subject_name = serializers.CharField(source="subject.name", read_only=True, default=None)
    uploaded_by_name = serializers.SerializerMethodField()
    characters = serializers.SerializerMethodField()

    class Meta:
        model = ClassLibraryItem
        fields = (
            "public_id",
            "organization",
            "organization_name",
            "classroom",
            "classroom_name",
            "subject",
            "subject_name",
            "title",
            "description",
            "category",
            "original_filename",
            "file_size",
            "extension",
            "source_type",
            "status",
            "uploaded_by_name",
            "characters",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_uploaded_by_name(self, obj) -> str | None:
        user = obj.uploaded_by
        return (user.full_name or user.email) if user else None

    def get_characters(self, obj) -> list[str]:
        return list(library.characters_for(obj))


class LibraryItemCreateSerializer(serializers.Serializer):
    file = serializers.FileField(allow_empty_file=False)
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    category = serializers.ChoiceField(choices=CATEGORY_CHOICES, default=ClassLibraryItem.Category.HANDOUT)
    organization = serializers.UUIDField(required=False, allow_null=True)
    classroom = serializers.UUIDField(required=False, allow_null=True)
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True), required=False, allow_null=True
    )

    def validate_file(self, value):
        self.context["file_metadata"] = validate_student_source_file(value)
        return value

    def validate(self, attrs):
        organization, classroom = library.resolve_upload_target(
            self.context["request"].user, attrs.get("organization"), attrs.get("classroom")
        )
        attrs["organization"] = organization
        attrs["classroom"] = classroom
        return attrs


class LibraryItemUpdateSerializer(serializers.ModelSerializer):
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True), required=False, allow_null=True
    )

    class Meta:
        model = ClassLibraryItem
        fields = ("title", "description", "category", "subject", "status")


class LibraryUseRequestSerializer(serializers.Serializer):
    character = serializers.ChoiceField(choices=[(c, c) for c in ("fahes", "kholasa", "khota", "sada")], required=False)
    project = serializers.UUIDField(required=False, allow_null=True)


class LibraryUseResponseSerializer(serializers.Serializer):
    source_id = serializers.IntegerField()
    project = serializers.UUIDField()
    project_title = serializers.CharField()
    character = serializers.CharField(allow_null=True)
    created = serializers.BooleanField()


# -- staff --------------------------------------------------------------------
@extend_schema(tags=["Class Library"])
class AdminClassLibraryViewSet(
    scope_policy.TenantScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Files a school or a class shares with its students."""

    tenant_user_field = scope_policy.TenantScopedQuerysetMixin.SCOPED_BY_ORGANIZATION
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    permission_map = {
        "list": library.VIEW_PERMISSION,
        "retrieve": library.VIEW_PERMISSION,
        "download": library.VIEW_PERMISSION,
        "create": library.MANAGE_PERMISSION,
        "partial_update": library.MANAGE_PERMISSION,
        "destroy": library.MANAGE_PERMISSION,
    }
    required_scope_types = ("global", "organization", "class")
    lookup_field = "public_id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = LibraryItemSerializer

    def get_required_permission(self):
        return self.permission_map.get(getattr(self, "action", None))

    def get_queryset(self):
        queryset = ClassLibraryItem.objects.select_related("organization", "classroom", "subject", "uploaded_by")
        queryset = library.scope_items_for_staff(self.request.user, queryset, self.get_required_permission())
        params = self.request.query_params
        # Filters narrow what the scope already allows; never widen it.
        if params.get("organization"):
            queryset = queryset.filter(organization__public_id=params["organization"])
        if params.get("classroom"):
            queryset = queryset.filter(classroom__public_id=params["classroom"])
        if params.get("category"):
            queryset = queryset.filter(category=params["category"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        if params.get("search"):
            queryset = queryset.filter(title__icontains=params["search"].strip())
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter("organization", OpenApiTypes.UUID),
            OpenApiParameter("classroom", OpenApiTypes.UUID),
            OpenApiParameter("category", OpenApiTypes.STR),
            OpenApiParameter("status", OpenApiTypes.STR),
            OpenApiParameter("search", OpenApiTypes.STR),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=LibraryItemCreateSerializer, responses={201: LibraryItemSerializer})
    def create(self, request, *args, **kwargs):
        serializer = LibraryItemCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        meta = serializer.context["file_metadata"]
        with transaction.atomic():
            item = ClassLibraryItem(
                organization=data["organization"],
                classroom=data["classroom"],
                subject=data.get("subject"),
                title=data["title"],
                description=data.get("description", ""),
                category=data["category"],
                original_filename=meta["original_filename"],
                file_size=meta["file_size"],
                mime_type=meta["mime_type"],
                extension=meta["extension"],
                source_type=meta["source_type"],
                uploaded_by=request.user,
            )
            item.file = data["file"]
            item.save()
            item.sha256 = library.sha256_of(item.file)
            item.save(update_fields=["sha256", "updated_at"])
            log_admin_action(
                request.user,
                "library.item_uploaded",
                target=item,
                metadata={"organization": str(item.organization.public_id), "classroom": str(item.classroom.public_id) if item.classroom else None},
                request=request,
            )
            transaction.on_commit(lambda: library.notify_students(item))
        return Response(LibraryItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=LibraryItemUpdateSerializer, responses=LibraryItemSerializer)
    def partial_update(self, request, *args, **kwargs):
        item = self.get_object()
        serializer = LibraryItemUpdateSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        item = serializer.save()
        log_admin_action(request.user, "library.item_updated", target=item, request=request)
        return Response(LibraryItemSerializer(item).data)

    def perform_destroy(self, instance):
        # Students' copies are their own files and stay; only the shared
        # original goes.
        file_field = instance.file
        log_admin_action(self.request.user, "library.item_deleted", target=instance, request=self.request)
        instance.delete()
        if file_field:
            file_field.delete(save=False)

    @extend_schema(responses={(200, "application/octet-stream"): bytes})
    @action(detail=True, methods=["get"])
    def download(self, request, public_id=None):
        return _file_response(self.get_object())


# -- students -----------------------------------------------------------------
@extend_schema(tags=["Class Library"])
class StudentClassLibraryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """The shared library of the learner's own classes and organizations."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = LibraryItemSerializer
    lookup_field = "public_id"

    def get_queryset(self):
        queryset = library.items_for_student(self.request.user)
        params = self.request.query_params
        if params.get("classroom"):
            queryset = queryset.filter(classroom__public_id=params["classroom"])
        if params.get("category"):
            queryset = queryset.filter(category=params["category"])
        if params.get("subject"):
            queryset = queryset.filter(subject_id=params["subject"])
        if params.get("search"):
            queryset = queryset.filter(title__icontains=params["search"].strip())
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter("classroom", OpenApiTypes.UUID),
            OpenApiParameter("category", OpenApiTypes.STR),
            OpenApiParameter("subject", OpenApiTypes.INT),
            OpenApiParameter("search", OpenApiTypes.STR),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={(200, "application/octet-stream"): bytes})
    @action(detail=True, methods=["get"])
    def download(self, request, public_id=None):
        return _file_response(self.get_object())

    @extend_schema(request=LibraryUseRequestSerializer, responses={200: LibraryUseResponseSerializer, 201: LibraryUseResponseSerializer})
    @action(detail=True, methods=["post"])
    def use(self, request, public_id=None):
        """Put the item in one of my projects (once), ready for a character."""
        item = self.get_object()
        serializer = LibraryUseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        character = serializer.validated_data.get("character")
        if character and character not in library.characters_for(item):
            raise serializers.ValidationError(
                {"character": "هذه الشخصية لا تعمل على هذا النوع من الملفات."}
            )
        source, created = library.copy_for_student(
            request.user, item, project_public_id=serializer.validated_data.get("project")
        )
        payload = {
            "source_id": source.pk,
            "project": source.project.public_id,
            "project_title": source.project.title,
            "character": character,
            "created": created,
        }
        return Response(
            LibraryUseResponseSerializer(payload).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _file_response(item):
    if not item.file:
        raise Http404
    response = FileResponse(
        item.file.open("rb"),
        as_attachment=True,
        filename=item.original_filename or f"library-{item.public_id}{item.extension}",
        content_type=item.mime_type or "application/octet-stream",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
