from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.sources.models import StudentSource

from .models import DeskNote, DeskPreference, FocusSession, ReadingPosition, SessionTask
from .serializers import (
    DeskHomeSerializer,
    DeskNoteSerializer,
    DeskNoteWriteSerializer,
    DeskPreferenceSerializer,
    FocusSessionSerializer,
    FocusSessionStartSerializer,
    ReadingPositionSerializer,
    ReadingPositionUpdateSerializer,
    SessionTaskSerializer,
)
from .services import continue_reading, running_focus_session, stop_focus_session, today_tasks


@extend_schema(tags=['My Desk'])
class DeskHomeView(APIView):
    """"ماذا سأُنجز في هذه الجلسة؟" -- one call for the desk's landing state."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=DeskHomeSerializer)
    def get(self, request):
        position = continue_reading(request.user)
        running = running_focus_session(request.user)
        preference, _ = DeskPreference.objects.get_or_create(user=request.user)
        return Response(
            {
                'continue_reading': ReadingPositionSerializer(position).data if position else None,
                'today_tasks': SessionTaskSerializer(today_tasks(request.user), many=True).data,
                'running_focus_session': FocusSessionSerializer(running).data if running else None,
                'preferences': DeskPreferenceSerializer(preference).data,
            }
        )


@extend_schema(tags=['My Desk'])
class DeskNoteViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = DeskNote.objects.filter(user=self.request.user).select_related('source', 'project')
        params = self.request.query_params
        if params.get('source'):
            queryset = queryset.filter(source_id=params['source'])
        if params.get('note_type'):
            queryset = queryset.filter(note_type=params['note_type'])
        return queryset

    def get_serializer_class(self):
        return DeskNoteWriteSerializer if self.action in {'create', 'partial_update'} else DeskNoteSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        return Response(DeskNoteSerializer(instance).data)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(DeskNoteSerializer(instance).data)


@extend_schema(tags=['My Desk'])
class ReadingPositionView(APIView):
    """GET/PUT the learner's own position in one source (owner-checked)."""

    permission_classes = [permissions.IsAuthenticated]

    def _source(self, request, source_id):
        return get_object_or_404(StudentSource, pk=source_id, user=request.user)

    @extend_schema(responses=ReadingPositionSerializer)
    def get(self, request, source_id):
        self._source(request, source_id)
        position = ReadingPosition.objects.filter(user=request.user, source_id=source_id).select_related('source').first()
        if position is None:
            return Response({'source': int(source_id), 'position': {}, 'updated_at': None})
        return Response(ReadingPositionSerializer(position).data)

    @extend_schema(request=ReadingPositionUpdateSerializer, responses=ReadingPositionSerializer)
    def put(self, request, source_id):
        source = self._source(request, source_id)
        position_payload = request.data.get('position')
        if not isinstance(position_payload, dict):
            return Response({'position': 'A position object is required.'}, status=status.HTTP_400_BAD_REQUEST)
        position, _ = ReadingPosition.objects.update_or_create(
            user=request.user, source=source, defaults={'position': position_payload}
        )
        return Response(ReadingPositionSerializer(position).data)


@extend_schema(tags=['My Desk'])
class FocusSessionViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = FocusSessionSerializer

    def get_queryset(self):
        return FocusSession.objects.filter(user=self.request.user).select_related('subject', 'project')

    @extend_schema(request=FocusSessionStartSerializer, responses={201: FocusSessionSerializer})
    def create(self, request, *args, **kwargs):
        # A second running timer is confusing, not useful: stop the stale one.
        stale = running_focus_session(request.user)
        if stale is not None:
            stop_focus_session(stale, status=FocusSession.Status.CANCELLED)
        serializer = FocusSessionStartSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        session = FocusSession.objects.create(user=request.user, started_at=timezone.now(), **serializer.validated_data)
        return Response(FocusSessionSerializer(session).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def stop(self, request, pk=None):
        session = self.get_object()
        if session.status != FocusSession.Status.RUNNING:
            return Response({'detail': 'Session is not running.'}, status=status.HTTP_409_CONFLICT)
        stop_focus_session(session, status=FocusSession.Status.COMPLETED)
        return Response(FocusSessionSerializer(session).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        session = self.get_object()
        if session.status != FocusSession.Status.RUNNING:
            return Response({'detail': 'Session is not running.'}, status=status.HTTP_409_CONFLICT)
        stop_focus_session(session, status=FocusSession.Status.CANCELLED)
        return Response(FocusSessionSerializer(session).data)


@extend_schema(tags=['My Desk'])
class SessionTaskViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SessionTaskSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = SessionTask.objects.filter(user=self.request.user)
        task_date = self.request.query_params.get('task_date')
        if task_date:
            queryset = queryset.filter(task_date=task_date)
        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=['My Desk'])
class DeskPreferenceView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=DeskPreferenceSerializer)
    def get(self, request):
        preference, _ = DeskPreference.objects.get_or_create(user=request.user)
        return Response(DeskPreferenceSerializer(preference).data)

    @extend_schema(request=DeskPreferenceSerializer, responses=DeskPreferenceSerializer)
    def patch(self, request):
        preference, _ = DeskPreference.objects.get_or_create(user=request.user)
        serializer = DeskPreferenceSerializer(preference, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
