from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import SupportMessage, SupportTicket


class SupportMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)

    class Meta:
        model = SupportMessage
        fields = ("id", "sender", "sender_name", "body", "is_internal", "created_at")
        read_only_fields = ("id", "sender", "sender_name", "is_internal", "created_at")


class SupportTicketListSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = SupportTicket
        fields = ("id", "subject", "category", "priority", "status", "message_count", "created_at", "updated_at")
        read_only_fields = ("id", "status", "message_count", "created_at", "updated_at")


class SupportTicketDetailSerializer(serializers.ModelSerializer):
    messages = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = ("id", "subject", "category", "priority", "status", "metadata", "messages", "resolved_at", "created_at", "updated_at")
        read_only_fields = ("id", "status", "messages", "resolved_at", "created_at", "updated_at")

    @extend_schema_field(SupportMessageSerializer(many=True))
    def get_messages(self, obj):
        return SupportMessageSerializer(obj.messages.filter(is_internal=False), many=True).data


class SupportTicketCreateSerializer(serializers.ModelSerializer):
    message = serializers.CharField(write_only=True)

    class Meta:
        model = SupportTicket
        fields = ("id", "subject", "category", "priority", "message", "metadata")
        read_only_fields = ("id",)

    @transaction.atomic
    def create(self, validated_data):
        message = validated_data.pop("message")
        ticket = SupportTicket.objects.create(user=self.context["request"].user, **validated_data)
        SupportMessage.objects.create(ticket=ticket, sender=ticket.user, body=message)
        return ticket


class SupportMessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=10000)
