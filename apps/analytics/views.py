from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import StudentRecommendation
from .serializers import StudentRecommendationSerializer


class StudentRecommendationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StudentRecommendationSerializer
    ordering = ('-created_at',)

    def get_queryset(self):
        queryset = StudentRecommendation.objects.filter(user=self.request.user).select_related('subject')
        subject = self.request.query_params.get('subject')
        if subject:
            queryset = queryset.filter(subject_id=subject)
        return queryset

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        item = self.get_object()
        if not item.is_read:
            item.is_read = True
            item.save(update_fields=['is_read', 'updated_at'])
        return Response(self.get_serializer(item).data)
