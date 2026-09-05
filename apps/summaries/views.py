from rest_framework import mixins, permissions, viewsets

from .models import Summary
from .serializers import SummarySerializer


class SummaryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SummarySerializer

    def get_queryset(self):
        queryset = Summary.objects.filter(user=self.request.user).select_related('source', 'collection')
        source = self.request.query_params.get('source')
        collection = self.request.query_params.get('collection')
        if source:
            queryset = queryset.filter(source_id=source)
        if collection:
            queryset = queryset.filter(collection_id=collection)
        return queryset
