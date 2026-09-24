from django.conf import settings
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers, status
from rest_framework.views import APIView

from .health import readiness_payload
from .responses import error_response, success_response

HEALTH_RESPONSE = inline_serializer(
    name="HealthResponse",
    fields={
        "success": serializers.BooleanField(),
        "message": serializers.CharField(),
        "data": serializers.JSONField(),
    },
)


class LivenessView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(tags=["System"], responses={200: HEALTH_RESPONSE})
    def get(self, request):
        return success_response(
            data={
                "status": "ok",
                "service": "baraq_backend",
                "version": settings.APP_VERSION,
            },
            message="Service is alive.",
        )


class ReadinessView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(tags=["System"], responses={200: HEALTH_RESPONSE, 503: HEALTH_RESPONSE})
    def get(self, request):
        checks, ready = readiness_payload()
        data = {
            "status": "ready" if ready else "degraded",
            "service": "baraq_backend",
            "version": settings.APP_VERSION,
            "checks": checks,
        }
        if not ready:
            return error_response(
                message="Service is not ready.",
                errors=data,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="service_not_ready",
            )
        return success_response(data=data, message="Service is ready.")


class HealthCheckView(ReadinessView):
    """Backward-compatible readiness endpoint."""


META_RESPONSE = inline_serializer(
    name="ProjectMetaResponse",
    fields={
        "success": serializers.BooleanField(),
        "message": serializers.CharField(),
        "data": serializers.JSONField(),
    },
)


class ProjectMetaView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(tags=["System"], responses={200: META_RESPONSE})
    def get(self, request):
        return success_response(
            data={
                "name": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "api_version": settings.API_VERSION,
                "environment": settings.ENVIRONMENT,
                "features": settings.APP_FEATURES,
            },
            message="Project metadata loaded successfully.",
        )
