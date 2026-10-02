"""ساحة المعرفة / صفي's lesson-linked Q&A: one classroom-scoped mechanism.

Students reach it through active membership (plain IsAuthenticated), the
same as the Classroom Shared Library and class work. Staff reach it for
moderation only (knowledge.view / knowledge.moderate, scoped exactly like
the library): this is student-authored content a teacher may need to
remove, not content a teacher manages day to day.
"""

from django.db.models import Count
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.admin_dashboard.permissions import HasAdminPermission, IsAdminDashboardUser
from apps.organizations import scope as scope_policy

from .models import KnowledgeReply, KnowledgeThread, SavedThread
from .serializers import (
    AcceptReplySerializer,
    HelpfulToggleResponseSerializer,
    KnowledgeReplyCreateSerializer,
    KnowledgeReplySerializer,
    KnowledgeThreadCreateSerializer,
    KnowledgeThreadSerializer,
    KnowledgeThreadUpdateSerializer,
    SavedToggleResponseSerializer,
)
from .services import (
    MODERATE_PERMISSION,
    VIEW_PERMISSION,
    accept_reply,
    can_moderate,
    reachable_classroom_ids,
    require_edit_rights,
    resolve_classroom_for_student,
    scope_rows_for_staff,
    thread_or_404_for_student,
    toggle_helpful,
    toggle_saved,
    visible_threads_for_student,
)


@extend_schema(tags=['Knowledge Square'])
class KnowledgeThreadViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = KnowledgeThreadSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'request': self.request}

    def get_queryset(self):
        queryset = (
            visible_threads_for_student(self.request.user)
            .select_related('classroom', 'subject', 'author')
            .annotate(reply_count=Count('replies', distinct=True))
            .order_by('-created_at')
        )
        params = self.request.query_params
        if params.get('classroom'):
            queryset = queryset.filter(classroom__public_id=params['classroom'])
        if params.get('subject'):
            queryset = queryset.filter(subject_id=params['subject'])
        if params.get('topic'):
            queryset = queryset.filter(topic__icontains=params['topic'])
        if params.get('status'):
            queryset = queryset.filter(status=params['status'])
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter('classroom', OpenApiTypes.UUID),
            OpenApiParameter('subject', OpenApiTypes.INT),
            OpenApiParameter('topic', OpenApiTypes.STR),
            OpenApiParameter('status', OpenApiTypes.STR),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=KnowledgeThreadCreateSerializer, responses={201: KnowledgeThreadSerializer})
    def create(self, request, *args, **kwargs):
        serializer = KnowledgeThreadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        classroom = resolve_classroom_for_student(request.user, data.pop('classroom'))
        thread = KnowledgeThread.objects.create(
            classroom=classroom, author=request.user, is_teacher_content=can_moderate(request.user, classroom), **data
        )
        return Response(KnowledgeThreadSerializer(thread, context={'request': request}).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        thread = self.get_object()
        require_edit_rights(request.user, thread)
        serializer = KnowledgeThreadUpdateSerializer(thread, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(KnowledgeThreadSerializer(thread, context={'request': request}).data)

    def destroy(self, request, *args, **kwargs):
        thread = self.get_object()
        require_edit_rights(request.user, thread)
        thread.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=AcceptReplySerializer, responses=KnowledgeThreadSerializer)
    @action(detail=True, methods=['post'], url_path='accept-reply', url_name='accept-reply')
    def accept_reply_action(self, request, pk=None):
        thread = self.get_object()
        serializer = AcceptReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reply = get_object_or_404(KnowledgeReply, pk=serializer.validated_data['reply'])
        accept_reply(request.user, thread, reply)
        return Response(KnowledgeThreadSerializer(thread, context={'request': request}).data)

    @extend_schema(request=None, responses=SavedToggleResponseSerializer)
    @action(detail=True, methods=['post'], url_path='save', url_name='save')
    def save_action(self, request, pk=None):
        thread = self.get_object()
        is_saved = toggle_saved(request.user, thread)
        return Response(SavedToggleResponseSerializer({'is_saved': is_saved}).data)


@extend_schema(tags=['Knowledge Square'])
class MySavedThreadsView(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = KnowledgeThreadSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'request': self.request}

    def get_queryset(self):
        thread_ids = SavedThread.objects.filter(user=self.request.user).values_list('thread_id', flat=True)
        return KnowledgeThread.objects.filter(pk__in=thread_ids).select_related('classroom', 'subject', 'author')


@extend_schema(tags=['Knowledge Square'])
class KnowledgeReplyViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = KnowledgeReplySerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'request': self.request}

    def get_queryset(self):
        thread_id = self.request.query_params.get('thread')
        if not thread_id:
            raise serializers.ValidationError({'thread': 'This field is required.'})
        thread_or_404_for_student(self.request.user, thread_id)
        return KnowledgeReply.objects.filter(thread_id=thread_id).select_related('author', 'thread')

    def create(self, request, *args, **kwargs):
        serializer = KnowledgeReplyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        thread = thread_or_404_for_student(request.user, data['thread'])
        reply = KnowledgeReply.objects.create(
            thread=thread, author=request.user, body=data['body'], is_teacher_reply=can_moderate(request.user, thread.classroom)
        )
        return Response(KnowledgeReplySerializer(reply, context={'request': request}).data, status=status.HTTP_201_CREATED)

    def _require_reply_edit_rights(self, user, reply):
        if reply.author_id == user.id or can_moderate(user, reply.thread.classroom):
            return
        raise PermissionDenied('Only the author or a class moderator can change this.')

    def _visible_reply_or_404(self, pk):
        # Scoped by reach *before* the edit-rights check: a reply in a
        # class this caller cannot even see must read as not-found, never
        # as a 403 that confirms the row exists.
        return get_object_or_404(
            KnowledgeReply.objects.filter(thread__classroom_id__in=reachable_classroom_ids(self.request.user)), pk=pk
        )

    def partial_update(self, request, *args, **kwargs):
        reply = self._visible_reply_or_404(kwargs['pk'])
        self._require_reply_edit_rights(request.user, reply)
        body = request.data.get('body')
        if not body:
            raise serializers.ValidationError({'body': 'This field is required.'})
        reply.body = body
        reply.save(update_fields=['body', 'updated_at'])
        return Response(KnowledgeReplySerializer(reply, context={'request': request}).data)

    def destroy(self, request, *args, **kwargs):
        reply = self._visible_reply_or_404(kwargs['pk'])
        self._require_reply_edit_rights(request.user, reply)
        reply.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses=HelpfulToggleResponseSerializer)
    @action(detail=True, methods=['post'])
    def helpful(self, request, pk=None):
        reply = self._visible_reply_or_404(pk)
        is_helpful, helpful_count = toggle_helpful(request.user, reply)
        return Response(HelpfulToggleResponseSerializer({'is_helpful': is_helpful, 'helpful_count': helpful_count}).data)


# -- staff moderation (knowledge.view / knowledge.moderate) ------------------
@extend_schema(tags=['Knowledge Square'])
class AdminKnowledgeThreadViewSet(
    scope_policy.TenantScopedQuerysetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    tenant_user_field = scope_policy.TenantScopedQuerysetMixin.SCOPED_BY_ORGANIZATION
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    permission_map = {'list': VIEW_PERMISSION, 'retrieve': VIEW_PERMISSION, 'destroy': MODERATE_PERMISSION}
    required_scope_types = ('global', 'organization', 'class')
    serializer_class = KnowledgeThreadSerializer

    def get_required_permission(self):
        return self.permission_map.get(getattr(self, 'action', None))

    def get_queryset(self):
        queryset = KnowledgeThread.objects.select_related('classroom', 'subject', 'author')
        return scope_rows_for_staff(self.request.user, queryset, self.get_required_permission())


@extend_schema(tags=['Knowledge Square'])
class AdminKnowledgeReplyViewSet(
    scope_policy.TenantScopedQuerysetMixin, mixins.ListModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    tenant_user_field = scope_policy.TenantScopedQuerysetMixin.SCOPED_BY_ORGANIZATION
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    permission_map = {'list': VIEW_PERMISSION, 'destroy': MODERATE_PERMISSION}
    required_scope_types = ('global', 'organization', 'class')
    serializer_class = KnowledgeReplySerializer

    def get_required_permission(self):
        return self.permission_map.get(getattr(self, 'action', None))

    def get_queryset(self):
        queryset = KnowledgeReply.objects.select_related('author', 'thread__classroom')
        return scope_rows_for_staff(self.request.user, queryset, self.get_required_permission(), classroom_field='thread__classroom')
