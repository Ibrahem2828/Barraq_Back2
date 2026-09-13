from rest_framework import serializers

from .models import WaitlistEntry


class WaitlistJoinSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    full_name = serializers.CharField(max_length=150, trim_whitespace=True)
    locale = serializers.ChoiceField(choices=WaitlistEntry.Locale.choices, default=WaitlistEntry.Locale.AR)
    # Honeypot: a real visitor never fills this (it's visually hidden on the
    # site); a non-empty value means a bot filled every field it could find.
    company = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_company(self, value):
        if value.strip():
            raise serializers.ValidationError("Spam detected.")
        return value


class WaitlistCountSerializer(serializers.Serializer):
    count = serializers.IntegerField()


class WaitlistJoinResponseSerializer(serializers.Serializer):
    created = serializers.BooleanField()
    count = serializers.IntegerField()
    message = serializers.CharField()
