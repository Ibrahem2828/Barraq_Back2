from django.utils import timezone
from rest_framework import filters, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.admin_dashboard.permissions import HasAdminPermission, IsAdminDashboardUser

from .models import SupportMessage, SupportTicket


class AdminSupportMessageSerializer(serializers.ModelSerializer):
    sender_email = serializers.EmailField(source="sender.email", read_only=True)

    class Meta:
        model = SupportMessage
        fields = ("id", "sender", "sender_email", "body", "is_internal", "created_at")
        read_only_fields = fields


class AdminSupportTicketSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    assigned_to_email = serializers.EmailField(source="assigned_to.email", read_only=True, allow_null=True)
    messages = AdminSupportMessageSerializer(many=True, read_only=True)

    class Meta:
        model = SupportTicket
        fields = (
            "id", "user", "user_email", "assigned_to", "assigned_to_email", "subject", "category", "priority",
            "status", "metadata", "resolved_at", "messages", "created_at", "updated_at",
        )
        read_only_fields = ("id", "user", "user_email", "assigned_to_email", "messages", "created_at", "updated_at")

    def validate_assigned_to(self, value):
        if value is not None and not (value.is_staff or value.role in {value.Roles.SUPPORT, value.Roles.ADMIN, value.Roles.SUPER_ADMIN}):
            raise serializers.ValidationError("Tickets may be assigned only to staff or support users.")
        return value

    def validate(self, attrs):
        status_value = attrs.get("status", getattr(self.instance, "status", None))
        if status_value in {SupportTicket.Status.RESOLVED, SupportTicket.Status.CLOSED}:
            attrs["resolved_at"] = attrs.get("resolved_at") or timezone.now()
        elif "status" in attrs:
            attrs["resolved_at"] = None
        return attrs

class AdminSupportMessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=10000)
    is_internal = serializers.BooleanField(default=False)


class AdminSupportTicketViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = "support.view"
    serializer_class = AdminSupportTicketSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["subject", "user__email", "messages__body"]
    ordering_fields = ["created_at", "updated_at", "priority", "status"]
    ordering = ["-updated_at"]
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_required_permission(self):
        return "support.manage" if self.action in {"partial_update", "add_message"} else self.required_permission

    def get_queryset(self):
        queryset = SupportTicket.objects.select_related("user", "assigned_to").prefetch_related("messages__sender")
        for field in ("status", "priority", "category"):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    @action(detail=True, methods=["post"], url_path="messages")
    def add_message(self, request, pk=None):
        ticket = self.get_object()
        serializer = AdminSupportMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        SupportMessage.objects.create(ticket=ticket, sender=request.user, **serializer.validated_data)
        if not serializer.validated_data["is_internal"]:
            ticket.status = SupportTicket.Status.WAITING_USER
            ticket.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(ticket).data)

    def perform_update(self, serializer):
        ticket = serializer.save()
        if ticket.status in {SupportTicket.Status.RESOLVED, SupportTicket.Status.CLOSED} and ticket.resolved_at is None:
            ticket.resolved_at = timezone.now()
            ticket.save(update_fields=["resolved_at", "updated_at"])
