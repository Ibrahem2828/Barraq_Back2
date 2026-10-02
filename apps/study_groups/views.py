"""رفاق برّاق (Study Buddies): small, invite-only groups.

Plain IsAuthenticated throughout -- a group is not a tenant and has no
admin surface. Visibility is membership, not ownership: `get_object` on a
non-member's group (or a row inside one) resolves as not found everywhere
below, via `require_membership`/`require_owner`. Flat routing with `group`
as a body/query field, the same convention apps.class_work uses for
`classroom` -- no nested-router URL kwargs.
"""

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.users.models import User

from .models import GroupGoal, GroupMembership, GroupMessage, GroupSession, GroupTask, MessageReport, StudyGroup
from .serializers import (
    BanMemberSerializer,
    GroupGoalSerializer,
    GroupMembershipSerializer,
    GroupMessageCreateSerializer,
    GroupMessageSerializer,
    GroupSessionCreateSerializer,
    GroupSessionSerializer,
    GroupTaskSerializer,
    JoinGroupSerializer,
    ReportMessageSerializer,
    SessionStatusSerializer,
    StudyGroupCreateSerializer,
    StudyGroupSerializer,
)
from .services import (
    AUTO_FLAG_REPORT_THRESHOLD,
    active_membership,
    ban_member,
    create_group,
    end_session,
    join_group,
    leave_group,
    my_group_ids,
    require_membership,
    require_owner,
    session_status,
    start_session,
)


def _resolve_group(user, group_id):
    group = get_object_or_404(StudyGroup, pk=group_id)
    require_membership(user, group)
    return group


@extend_schema(tags=['Study Buddies'])
class StudyGroupViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StudyGroupSerializer

    def get_queryset(self):
        return StudyGroup.objects.filter(id__in=my_group_ids(self.request.user)).annotate(
            member_count=Count('memberships', filter=Q(memberships__status='active'), distinct=True)
        )

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'request': self.request}

    @extend_schema(request=StudyGroupCreateSerializer, responses={201: StudyGroupSerializer})
    def create(self, request, *args, **kwargs):
        serializer = StudyGroupCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group = create_group(request.user, **serializer.validated_data)
        return Response(self.get_serializer(self.get_queryset().get(pk=group.pk)).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        group = self.get_object()
        require_owner(request.user, group)
        name = request.data.get('name')
        max_members = request.data.get('max_members')
        if name:
            group.name = name
        if max_members:
            group.max_members = max_members
        group.save(update_fields=['name', 'max_members', 'updated_at'])
        return Response(self.get_serializer(group).data)

    @extend_schema(request=JoinGroupSerializer, responses={200: StudyGroupSerializer})
    @action(detail=False, methods=['post'])
    def join(self, request):
        serializer = JoinGroupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group, _membership = join_group(request.user, **serializer.validated_data)
        return Response(self.get_serializer(self.get_queryset().get(pk=group.pk)).data)

    @action(detail=True, methods=['post'])
    def leave(self, request, pk=None):
        group = self.get_object()
        membership = require_membership(request.user, group)
        leave_group(membership)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses=GroupMembershipSerializer(many=True))
    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        group = self.get_object()
        require_membership(request.user, group)
        rows = group.memberships.filter(status='active').select_related('user').order_by('-role', 'joined_at')
        return Response(GroupMembershipSerializer(rows, many=True).data)

    @extend_schema(request=BanMemberSerializer, responses={204: None})
    @action(detail=True, methods=['post'])
    def ban(self, request, pk=None):
        group = self.get_object()
        require_owner(request.user, group)
        serializer = BanMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = get_object_or_404(User, pk=data.pop('user'))
        ban_member(request.user, group, target, **data)
        return Response(status=status.HTTP_204_NO_CONTENT)


class _GroupScopedViewSet(viewsets.ModelViewSet):
    """Any member may read and create; only the row's creator or the
    group's owner may edit or delete.

    Listing requires an explicit `group` (one group's goals/tasks, not
    everything across every group at once); a detail lookup by pk does
    not -- its membership check comes from the row's own group once
    fetched, the same as any other owner-or-member resource here.
    """

    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    model: type  # the row model, e.g. GroupGoal -- not the related_name

    def get_queryset(self):
        queryset = self.model.objects.filter(group_id__in=my_group_ids(self.request.user))
        group_id = self.request.query_params.get('group') or self.request.data.get('group')
        if group_id:
            queryset = queryset.filter(group_id=group_id)
        return queryset

    @extend_schema(parameters=[OpenApiParameter('group', OpenApiTypes.INT, required=True)])
    def list(self, request, *args, **kwargs):
        if not request.query_params.get('group'):
            raise ValidationError({'group': 'This field is required.'})
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        group = _resolve_group(self.request.user, self.request.data.get('group'))
        serializer.save(group=group, created_by=self.request.user)

    def _check_can_edit(self, instance):
        membership = active_membership(self.request.user, instance.group)
        if membership is None:
            raise ValidationError('Group not found.')
        is_mine = instance.created_by_id == self.request.user.id
        if not (is_mine or membership.role == GroupMembership.Role.OWNER):
            raise PermissionDenied('Only the creator or the group owner can change this.')

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        self._check_can_edit(instance)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self._check_can_edit(instance)
        return super().destroy(request, *args, **kwargs)


@extend_schema(tags=['Study Buddies'])
class GroupGoalViewSet(_GroupScopedViewSet):
    serializer_class = GroupGoalSerializer
    model = GroupGoal


@extend_schema(tags=['Study Buddies'])
class GroupTaskViewSet(_GroupScopedViewSet):
    serializer_class = GroupTaskSerializer
    model = GroupTask


@extend_schema(tags=['Study Buddies'])
class GroupSessionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = GroupSessionSerializer

    def get_queryset(self):
        queryset = GroupSession.objects.filter(group_id__in=my_group_ids(self.request.user)).select_related('summary')
        group_id = self.request.query_params.get('group') or self.request.data.get('group')
        if group_id:
            queryset = queryset.filter(group_id=group_id)
        return queryset

    @extend_schema(parameters=[OpenApiParameter('group', OpenApiTypes.INT, required=True)])
    def list(self, request, *args, **kwargs):
        if not request.query_params.get('group'):
            raise ValidationError({'group': 'This field is required.'})
        return super().list(request, *args, **kwargs)

    @extend_schema(request=GroupSessionCreateSerializer, responses={201: GroupSessionSerializer})
    def create(self, request, *args, **kwargs):
        group = _resolve_group(request.user, request.data.get('group'))
        serializer = GroupSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = group.sessions.create(created_by=request.user, **serializer.validated_data)
        return Response(GroupSessionSerializer(session).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses=SessionStatusSerializer)
    @action(detail=True, methods=['get'], url_path='status', url_name='status')
    def status_view(self, request, pk=None):
        session = self.get_object()
        return Response(SessionStatusSerializer(session_status(session)).data)

    @extend_schema(responses=GroupSessionSerializer)
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        session = self.get_object()
        start_session(session)
        return Response(GroupSessionSerializer(session).data)

    @extend_schema(responses=GroupSessionSerializer)
    @action(detail=True, methods=['post'])
    def end(self, request, pk=None):
        session = self.get_object()
        end_session(session)
        return Response(GroupSessionSerializer(session).data)


@extend_schema(tags=['Study Buddies'])
class GroupMessageViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = GroupMessageSerializer

    def get_queryset(self):
        queryset = GroupMessage.objects.filter(group_id__in=my_group_ids(self.request.user)).select_related(
            'user', 'attached_source'
        )
        group_id = self.request.query_params.get('group') or self.request.data.get('group')
        if group_id:
            queryset = queryset.filter(group_id=group_id)
        return queryset

    @extend_schema(parameters=[OpenApiParameter('group', OpenApiTypes.INT, required=True)])
    def list(self, request, *args, **kwargs):
        if not request.query_params.get('group'):
            raise ValidationError({'group': 'This field is required.'})
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        group = _resolve_group(request.user, request.data.get('group'))
        serializer = GroupMessageCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        message = group.messages.create(user=request.user, **serializer.validated_data)
        return Response(GroupMessageSerializer(message).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ReportMessageSerializer, responses={204: None})
    @action(detail=True, methods=['post'])
    def report(self, request, pk=None):
        message = get_object_or_404(GroupMessage.objects.filter(group_id__in=my_group_ids(request.user)), pk=pk)
        serializer = ReportMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # get_or_create wraps its insert in its own savepoint, so a
        # duplicate report (unique together) rolls back only that attempt
        # instead of poisoning the whole request's transaction the way a
        # bare try/except IntegrityError around .create() would under
        # Postgres.
        MessageReport.objects.get_or_create(
            message=message, reported_by=request.user, defaults=serializer.validated_data
        )
        if message.reports.count() >= AUTO_FLAG_REPORT_THRESHOLD and not message.is_flagged:
            message.is_flagged = True
            message.save(update_fields=['is_flagged', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)
