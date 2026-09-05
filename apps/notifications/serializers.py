from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = (
            "id", "category", "title", "body", "data", "action_url", "read_at", "is_read", "created_at",
        )
        read_only_fields = fields
