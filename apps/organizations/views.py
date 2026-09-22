"""Scoped organization administration and the learner-facing join flow.

Every admin endpoint here resolves its target through `scope.py`. None of
them trusts an id from the request body or the query string: an
`organization` in a payload names a target, and naming a target is not the
same as being allowed to reach it.
"""

from __future__ import annotations

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_dashboard.permissions import HasAdminPermission, IsAdminDashboardUser
from apps.admin_dashboard.services import log_admin_action

from . import scope as scope_policy
from . import services
from .models import (
    ClassMembership,
    Classroom,
    Invitation,
    JoinRequest,
    Organization,
    OrganizationMembership,
)
from .serializers import (
    ClassMembershipSerializer,
    ClassroomSerializer,
    ClassroomWriteSerializer,
    InvitationCreateSerializer,
    InvitationSerializer,
    JoinPreviewRequestSerializer,
    JoinPreviewResponseSerializer,
    JoinRequestSerializer,
    MyMembershipSerializer,
    OrganizationMembershipSerializer,
    OrganizationSerializer,
    OrganizationWriteSerializer,
)


class ScopedAdminViewSet(
    scope_policy.TenantScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Base for every organization-scoped admin resource.

    List and retrieve only. Writes are declared per resource, as explicit
    methods, because each one has to re-resolve its target through the
    caller's scope -- a generic `create` would take the organization from
    the request body, which is the single most common way a tenant boundary
    is crossed. Invitations and join requests get no create route at all.

    Scope is applied in `get_queryset`, so list, retrieve, update, destroy and
    any custom action all inherit it from one place. A detail route that
    forgets to re-check is the classic tenant leak -- it only shows up when
    someone tries a neighbour's id, which is to say, in production.
    """

    # These resources carry the organization themselves, so the boundary is
    # applied in get_queryset rather than through a row's owner.
    tenant_user_field = scope_policy.TenantScopedQuerysetMixin.SCOPED_BY_ORGANIZATION
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    permission_map: dict[str, str] = {}
    required_permission: str | None = None
    required_scope_types: tuple[str, ...] = (
        "global",
        "organization",
        "class",
    )
    lookup_field = "public_id"

    def get_required_permission(self):
        return self.permission_map.get(getattr(self, "action", None), self.required_permission)


@extend_schema(tags=["Organizations"])
class OrganizationViewSet(ScopedAdminViewSet):
    serializer_class = OrganizationSerializer
    permission_map = {
        "list": "organizations.view",
        "retrieve": "organizations.view",
        "overview": "organizations.view",
        "members": "organizations.view",
        "create": "organizations.create",
        "partial_update": "organizations.update",
        "archive": "organizations.archive",
    }
    http_method_names = ["get", "post", "patch", "head", "options"]
    # A class grant is not permission to administer its parent organization.
    required_scope_types = ("global", "organization")

    def get_required_scope_types(self):
        # Creating a tenant is platform policy.  A local organization scope
        # only authorizes actions on that existing tenant.
        if self.action == "create":
            return ("global",)
        return self.required_scope_types

    def get_queryset(self):
        return scope_policy.scope_organizations(
            self.request.user,
            Organization.objects.all(),
            self.get_required_permission(),
        )

    def get_serializer_class(self):
        if self.action in {"create", "partial_update"}:
            return OrganizationWriteSerializer
        return OrganizationSerializer

    def create(self, request, *args, **kwargs):
        # Creating an organization is a platform action, not an in-tenant one:
        # a manager scoped to one school must not be able to conjure a second
        # one and become its manager. `organizations.create` is deliberately
        # absent from the seeded organization_manager role.
        if not scope_policy.has_global_scope(request.user, "organizations.create"):
            raise scope_policy.ScopeDenied("organization_access_denied")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = serializer.save(created_by=request.user)
        log_admin_action(request.user, "organization.created", target=organization, request=request)
        return Response(OrganizationSerializer(organization).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        organization = self.get_object()
        serializer = self.get_serializer(organization, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        organization = serializer.save()
        log_admin_action(request.user, "organization.updated", target=organization, request=request)
        return Response(OrganizationSerializer(organization).data)

    @action(detail=True, methods=["post"])
    def archive(self, request, public_id=None):
        organization = self.get_object()
        organization = services.archive_organization(organization=organization, archived_by=request.user)
        log_admin_action(request.user, "organization.archived", target=organization, request=request)
        return Response(OrganizationSerializer(organization).data)

    @action(detail=True, methods=["get"])
    def overview(self, request, public_id=None):
        """Counts for one organization, aggregated in the database.

        A scoped account never receives a platform total -- an aggregate
        leaks tenant shape as surely as a list does.
        """
        organization = self.get_object()
        return Response(services.organization_overview(organization))

    @action(detail=True, methods=["get"])
    def members(self, request, public_id=None):
        organization = self.get_object()
        memberships = (
            OrganizationMembership.objects.filter(organization=organization)
            .select_related("user")
            .order_by("-created_at")
        )
        page = self.paginate_queryset(memberships)
        serializer = OrganizationMembershipSerializer(page or memberships, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)


@extend_schema(tags=["Organizations"])
class ClassroomViewSet(ScopedAdminViewSet):
    serializer_class = ClassroomSerializer
    permission_map = {
        "list": "classes.view",
        "retrieve": "classes.view",
        "members": "class_members.view",
        "remove_member": "class_members.manage",
        "transfer_member": "class_members.manage",
        "invitations": "invitations.view",
        "create_invitation": "invitations.manage",
        "create": "classes.create",
        "partial_update": "classes.update",
        "archive": "classes.archive",
    }
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_required_scope_types(self):
        # A new classroom is addressed by its parent organization, so class A
        # scope cannot be used to create a sibling class in that organization.
        if self.action == "create":
            return ("global", "organization")
        return self.required_scope_types

    def get_queryset(self):
        queryset = Classroom.objects.select_related("organization").annotate(
            member_count=Count("memberships", filter=Q(memberships__status="active"), distinct=True)
        )
        queryset = scope_policy.scope_classrooms(self.request.user, queryset, self.get_required_permission())
        organization_public_id = self.request.query_params.get("organization")
        if organization_public_id:
            # A filter narrows what is already permitted. Applied after the
            # scope, never instead of it, so `?organization=<someone else's>`
            # can only ever return fewer rows -- never other rows.
            queryset = queryset.filter(organization__public_id=organization_public_id)
        return queryset

    def get_serializer_class(self):
        if self.action in {"create", "partial_update"}:
            return ClassroomWriteSerializer
        return ClassroomSerializer

    def create(self, request, *args, **kwargs):
        organization = get_object_or_404(Organization, public_id=request.data.get("organization"))
        # Re-resolved against the caller's scope. A manager of organization A
        # naming organization B in the body gets a 404, not a class in B.
        scope_policy.organization_for_write(request.user, organization, "classes.create")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        classroom = serializer.save(organization=organization, created_by=request.user)
        log_admin_action(request.user, "class.created", target=classroom, request=request)
        return Response(ClassroomSerializer(classroom).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        classroom = self.get_object()
        serializer = self.get_serializer(classroom, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        classroom = serializer.save()
        log_admin_action(request.user, "class.updated", target=classroom, request=request)
        return Response(ClassroomSerializer(classroom).data)

    @action(detail=True, methods=["post"])
    def archive(self, request, public_id=None):
        classroom = services.archive_classroom(classroom=self.get_object())
        log_admin_action(request.user, "class.archived", target=classroom, request=request)
        return Response(ClassroomSerializer(classroom).data)

    @action(detail=True, methods=["get"])
    def members(self, request, public_id=None):
        classroom = self.get_object()
        memberships = (
            ClassMembership.objects.filter(classroom=classroom)
            .select_related("user", "classroom")
            .order_by("-created_at")
        )
        page = self.paginate_queryset(memberships)
        serializer = ClassMembershipSerializer(page or memberships, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="members/remove")
    def remove_member(self, request, public_id=None):
        classroom = self.get_object()
        membership = get_object_or_404(ClassMembership, pk=request.data.get("membership"), classroom=classroom)
        membership = services.remove_class_member(membership=membership, removed_by=request.user)
        log_admin_action(request.user, "class.member_removed", target=classroom, request=request)
        return Response(ClassMembershipSerializer(membership).data)

    @action(detail=True, methods=["post"], url_path="members/transfer")
    def transfer_member(self, request, public_id=None):
        classroom = self.get_object()
        membership = get_object_or_404(ClassMembership, pk=request.data.get("membership"), classroom=classroom)
        target = get_object_or_404(Classroom, public_id=request.data.get("target_classroom"))
        # Both ends checked. Authorizing only the source class would let a
        # supervisor move a learner into a class they cannot see.
        scope_policy.assert_classroom_allowed(request.user, target, "class_members.manage")
        moved = services.transfer_class_member(membership=membership, target_classroom=target, moved_by=request.user)
        log_admin_action(request.user, "class.member_transferred", target=classroom, request=request)
        return Response(ClassMembershipSerializer(moved).data)

    @action(detail=True, methods=["get"])
    def invitations(self, request, public_id=None):
        classroom = self.get_object()
        invitations = Invitation.objects.filter(classroom=classroom).order_by("-created_at")
        return Response(InvitationSerializer(invitations, many=True).data)

    @action(detail=True, methods=["post"], url_path="invitations/create")
    def create_invitation(self, request, public_id=None):
        classroom = self.get_object()
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = Invitation.objects.create(
            organization=classroom.organization,
            classroom=classroom,
            expires_at=serializer.validated_data.get("expires_at"),
            max_uses=serializer.validated_data.get("max_uses") or 0,
            created_by=request.user,
        )
        # The audit record names the class, never the token or code: an audit
        # log that contains the credential is a second place to steal it from.
        log_admin_action(request.user, "invitation.created", target=classroom, request=request)
        return Response(InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Organizations"])
class InvitationViewSet(ScopedAdminViewSet):
    serializer_class = InvitationSerializer
    lookup_field = "pk"
    permission_map = {
        "list": "invitations.view",
        "retrieve": "invitations.view",
        "revoke": "invitations.manage",
    }
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        permission = self.get_required_permission()
        queryset = Invitation.objects.select_related("organization", "classroom")
        organization_ids = scope_policy.accessible_organization_ids(self.request.user, permission)
        if scope_policy.is_unrestricted(organization_ids):
            return queryset.order_by("-created_at")
        classroom_ids = scope_policy.accessible_classroom_ids(self.request.user, permission)
        # An organization grant reaches its invitations and every class below
        # it.  A class grant reaches only that class's invitations; it cannot
        # read an organization-wide credential or a sibling class's link.
        return queryset.filter(
            Q(organization_id__in=organization_ids) | Q(classroom_id__in=classroom_ids or [])
        ).order_by("-created_at")

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        invitation = self.get_object()
        invitation.status = Invitation.Status.REVOKED
        invitation.save(update_fields=["status", "updated_at"])
        log_admin_action(request.user, "invitation.revoked", target=invitation.classroom, request=request)
        return Response(InvitationSerializer(invitation).data)


@extend_schema(tags=["Organizations"])
class JoinRequestViewSet(ScopedAdminViewSet):
    serializer_class = JoinRequestSerializer
    permission_map = {
        "list": "join_requests.view",
        "retrieve": "join_requests.view",
        "approve": "join_requests.manage",
        "reject": "join_requests.manage",
    }
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        permission = self.get_required_permission()
        queryset = JoinRequest.objects.select_related("user", "organization", "classroom").order_by("-created_at")
        organization_ids = scope_policy.accessible_organization_ids(self.request.user, permission)
        if scope_policy.is_unrestricted(organization_ids):
            return self._apply_status_filter(queryset)
        classroom_ids = scope_policy.accessible_classroom_ids(self.request.user, permission)
        if not organization_ids and not classroom_ids:
            return queryset.none()
        # A class-scoped supervisor sees their class's requests; an
        # organization-scoped manager sees the organization's. The union is
        # what a holder of both should see, and nothing wider.  In particular,
        # ``organization_ids`` excludes parent ids inferred from class grants,
        # so an organization-wide request is not leaked to a class supervisor.
        queryset = queryset.filter(
            Q(classroom_id__in=classroom_ids or []) | Q(organization_id__in=organization_ids, classroom__isnull=True)
        )
        return self._apply_status_filter(queryset)

    def _apply_status_filter(self, queryset):
        status_value = self.request.query_params.get("status")
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    @action(detail=True, methods=["post"])
    def approve(self, request, public_id=None):
        join_request = self.get_object()
        join_request = services.approve_join_request(join_request=join_request, approved_by=request.user)
        log_admin_action(request.user, "join_request.approved", target=join_request, request=request)
        return Response(JoinRequestSerializer(join_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, public_id=None):
        join_request = self.get_object()
        join_request = services.reject_join_request(join_request=join_request, rejected_by=request.user)
        log_admin_action(request.user, "join_request.rejected", target=join_request, request=request)
        return Response(JoinRequestSerializer(join_request).data)


# --------------------------------------------------------------------------
# Learner-facing
# --------------------------------------------------------------------------


@extend_schema(
    tags=["Organizations"],
    request=JoinPreviewRequestSerializer,
    responses=JoinPreviewResponseSerializer,
)
class JoinPreviewView(APIView):
    """What a learner sees before committing to anything.

    Creates no membership and no request. Throttled, because this is the one
    endpoint that takes an invitation secret from an unauthenticated-ish
    position and says whether it is real.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "join_attempts"

    def post(self, request):
        serializer = JoinPreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = services.resolve_invitation(
            token=serializer.validated_data.get("token") or None,
            code=serializer.validated_data.get("code") or None,
        )
        return Response(services.preview_invitation(invitation))


@extend_schema(
    tags=["Organizations"],
    request=JoinPreviewRequestSerializer,
    responses=JoinRequestSerializer,
)
class JoinConfirmView(APIView):
    """The learner's explicit ask. Still creates no membership."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "join_attempts"

    def post(self, request):
        serializer = JoinPreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = services.resolve_invitation(
            token=serializer.validated_data.get("token") or None,
            code=serializer.validated_data.get("code") or None,
        )
        join_request, created = services.request_to_join(user=request.user, invitation=invitation)
        return Response(
            JoinRequestSerializer(join_request).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Organizations"],
    responses=MyMembershipSerializer(many=True),
)
class MyMembershipsView(APIView):
    """A learner's own memberships and pending requests.

    Scoped to `request.user` by construction; there is no id to supply and
    therefore nothing to tamper with.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        organizations = (
            OrganizationMembership.objects.filter(user=request.user)
            .select_related("organization")
            .order_by("-created_at")
        )
        classes = (
            ClassMembership.objects.filter(user=request.user)
            .select_related("classroom", "classroom__organization")
            .order_by("-created_at")
        )
        requests = (
            JoinRequest.objects.filter(user=request.user)
            .select_related("organization", "classroom")
            .order_by("-created_at")
        )
        return Response(
            {
                "organizations": [
                    {
                        "organization": {
                            "public_id": str(item.organization.public_id),
                            "name": item.organization.name,
                            "organization_type": item.organization.organization_type,
                        },
                        "member_type": item.member_type,
                        "status": item.status,
                        "joined_at": item.joined_at,
                    }
                    for item in organizations
                ],
                "classes": [
                    {
                        "classroom": {
                            "public_id": str(item.classroom.public_id),
                            "name": item.classroom.name,
                        },
                        "organization": {
                            "public_id": str(item.classroom.organization.public_id),
                            "name": item.classroom.organization.name,
                        },
                        "status": item.status,
                        "joined_at": item.joined_at,
                    }
                    for item in classes
                ],
                # Named, like the two lists above it. JoinRequestSerializer
                # addresses organizations by public_id because that is what
                # an admin client needs; a learner looking at their own
                # pending request needs to read the name of the school they
                # asked to join, not its uuid.
                "join_requests": [
                    {
                        "public_id": str(item.public_id),
                        "organization": {
                            "public_id": str(item.organization.public_id),
                            "name": item.organization.name,
                        },
                        "classroom": (
                            {
                                "public_id": str(item.classroom.public_id),
                                "name": item.classroom.name,
                            }
                            if item.classroom_id
                            else None
                        ),
                        "status": item.status,
                        "created_at": item.created_at,
                        "decided_at": item.decided_at,
                    }
                    for item in requests
                ],
            }
        )
