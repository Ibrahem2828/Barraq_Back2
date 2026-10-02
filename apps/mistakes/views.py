from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.summaries.models import Summary

from .models import MistakeEntry
from .serializers import (
    FlashcardReviewSerializer,
    ImportFromAttemptSerializer,
    MistakeEntryCreateSerializer,
    MistakeEntrySerializer,
    MistakeEntryUpdateSerializer,
    ReviewQueueItemSerializer,
    ReviewResultSerializer,
)
from .services import due_flashcards, due_mistakes, import_from_attempt, review_flashcard, review_mistake


@extend_schema(tags=['Mistakes Notebook'])
class MistakeEntryViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = MistakeEntry.objects.filter(user=self.request.user).select_related('subject', 'question')
        params = self.request.query_params
        if params.get('status'):
            queryset = queryset.filter(status=params['status'])
        if params.get('subject'):
            queryset = queryset.filter(subject_id=params['subject'])
        if params.get('category'):
            queryset = queryset.filter(category=params['category'])
        return queryset

    def get_serializer_class(self):
        if self.action == 'create':
            return MistakeEntryCreateSerializer
        if self.action == 'partial_update':
            return MistakeEntryUpdateSerializer
        return MistakeEntrySerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = serializer.save(user=request.user, next_review_at=timezone.now())
        return Response(MistakeEntrySerializer(entry).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(MistakeEntrySerializer(instance).data)

    @extend_schema(request=ImportFromAttemptSerializer, responses={201: MistakeEntrySerializer(many=True)})
    @action(detail=False, methods=['post'], url_path='import-from-attempt')
    def import_from_attempt_action(self, request):
        serializer = ImportFromAttemptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        created = import_from_attempt(request.user, serializer.validated_data['attempt'])
        return Response(MistakeEntrySerializer(created, many=True).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ReviewResultSerializer, responses=MistakeEntrySerializer)
    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        entry = self.get_object()
        if entry.status == MistakeEntry.Status.MASTERED:
            return Response({'detail': 'This entry is already mastered.'}, status=status.HTTP_409_CONFLICT)
        serializer = ReviewResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review_mistake(entry, result=serializer.validated_data['result'])
        return Response(MistakeEntrySerializer(entry).data)


@extend_schema(tags=['Mistakes Notebook'])
class FlashcardReviewView(APIView):
    """Reviews one flashcard of a summary the learner owns."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=FlashcardReviewSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        serializer = FlashcardReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        summary = get_object_or_404(Summary, pk=data['summary'], user=request.user)
        if data['flashcard_index'] >= len(summary.flashcards or []):
            return Response({'flashcard_index': 'No such flashcard.'}, status=status.HTTP_400_BAD_REQUEST)
        state = review_flashcard(request.user, summary, data['flashcard_index'], result=data['result'])
        return Response(
            {
                'summary': summary.id,
                'flashcard_index': state.flashcard_index,
                'box': state.box,
                'next_review_at': state.next_review_at,
                'review_count': state.review_count,
            }
        )


@extend_schema(tags=['Mistakes Notebook'])
class ReviewQueueView(APIView):
    """Today's merged review queue: due mistakes and due flashcards,
    oldest-due first. Neither kind needs a new AI call to be generated --
    both are read from content the learner already has."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        parameters=[OpenApiParameter('limit', OpenApiTypes.INT, description='Default 20, max 100.')],
        responses=ReviewQueueItemSerializer(many=True),
    )
    def get(self, request):
        try:
            limit = min(max(int(request.query_params.get('limit', 20)), 1), 100)
        except (TypeError, ValueError):
            limit = 20
        mistakes = due_mistakes(request.user, limit=limit)
        flashcards = due_flashcards(request.user, limit=limit)
        rows = [{'kind': 'mistake', 'mistake': entry, 'flashcard': None} for entry in mistakes]
        rows += [{'kind': 'flashcard', 'mistake': None, 'flashcard': row} for row in flashcards]

        def sort_key(row):
            if row['kind'] == 'mistake':
                return row['mistake'].next_review_at
            return row['flashcard']['next_review_at'] or timezone.now().replace(year=2000)

        rows.sort(key=sort_key)
        return Response(ReviewQueueItemSerializer(rows[:limit], many=True).data)
