from rest_framework import mixins, permissions, viewsets

from .models import Transcription
from .serializers import TranscriptionSerializer


class TranscriptionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TranscriptionSerializer

    def get_queryset(self):
        queryset = Transcription.objects.filter(user=self.request.user).select_related('source')
        source = self.request.query_params.get('source')
        if source:
            queryset = queryset.filter(source_id=source)
        return queryset
