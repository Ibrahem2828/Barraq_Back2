from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import WaitlistEntry
from .serializers import WaitlistCountSerializer, WaitlistJoinResponseSerializer, WaitlistJoinSerializer


@extend_schema(tags=["Waitlist"])
class WaitlistJoinView(APIView):
    """Public, unauthenticated endpoint the marketing site's waitlist form
    posts to. Joining twice with the same email is not an error -- it's
    treated as an idempotent "you're already on the list"."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "waitlist"

    @extend_schema(request=WaitlistJoinSerializer, responses=WaitlistJoinResponseSerializer)
    def post(self, request):
        serializer = WaitlistJoinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        full_name = serializer.validated_data["full_name"]
        locale = serializer.validated_data["locale"]

        entry, created = WaitlistEntry.objects.get_or_create(
            email=email,
            defaults={"full_name": full_name, "locale": locale, "source": WaitlistEntry.Source.WEBSITE},
        )
        if not created and full_name and entry.full_name != full_name:
            # Re-submitting with the same email is not an error (see the
            # docstring above) -- also let a corrected name overwrite a typo
            # from the first submission instead of silently ignoring it.
            entry.full_name = full_name
            entry.save(update_fields=["full_name", "updated_at"])
        count = WaitlistEntry.objects.count()
        message = _("Added to the waitlist.") if created else _("You're already on the waitlist.")
        return Response(
            {"created": created, "count": count, "message": str(message)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(tags=["Waitlist"])
class WaitlistCountView(APIView):
    """Public read-only counter the site displays as social proof."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(responses=WaitlistCountSerializer)
    def get(self, request):
        count = WaitlistEntry.objects.count()
        return Response(WaitlistCountSerializer({"count": count}).data)
