from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import SupportMessage, SupportTicket
from .serializers import (
    SupportMessageCreateSerializer,
    SupportTicketCreateSerializer,
    SupportTicketDetailSerializer,
    SupportTicketListSerializer,
)


@extend_schema(tags=["Support"])
class SupportTicketViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["subject"]
    ordering_fields = ["created_at", "updated_at", "priority"]
    ordering = ["-updated_at"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = SupportTicket.objects.filter(user=self.request.user).annotate(message_count=Count("messages"))
        ticket_status = self.request.query_params.get("status")
        if ticket_status:
            queryset = queryset.filter(status=ticket_status)
        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return SupportTicketCreateSerializer
        if self.action == "list":
            return SupportTicketListSerializer
        if self.action == "add_message":
            return SupportMessageCreateSerializer
        return SupportTicketDetailSerializer

    @action(detail=True, methods=["post"], url_path="messages")
    def add_message(self, request, pk=None):
        ticket = self.get_object()
        if ticket.status == SupportTicket.Status.CLOSED:
            raise ValidationError("لا يمكن إضافة رسالة إلى تذكرة مغلقة.")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        SupportMessage.objects.create(ticket=ticket, sender=request.user, body=serializer.validated_data["body"])
        if ticket.status == SupportTicket.Status.WAITING_USER:
            ticket.status = SupportTicket.Status.OPEN
            ticket.save(update_fields=["status", "updated_at"])
        return Response(SupportTicketDetailSerializer(ticket).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        ticket = self.get_object()
        ticket.status = SupportTicket.Status.CLOSED
        ticket.resolved_at = ticket.resolved_at or timezone.now()
        ticket.save(update_fields=["status", "resolved_at", "updated_at"])
        return Response(SupportTicketDetailSerializer(ticket).data)
