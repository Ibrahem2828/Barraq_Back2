from django.conf import settings
from django.views.generic import TemplateView
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers
from rest_framework.views import APIView

from .responses import success_response

API_ROOT_RESPONSE = inline_serializer(
    name="APIRootResponse",
    fields={
        "success": serializers.BooleanField(),
        "message": serializers.CharField(),
        "data": serializers.JSONField(),
    },
)


class LandingPageView(TemplateView):
    """Human-friendly service landing page so the public root never returns 404."""

    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        context.update(
            {
                "app_name": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "api_root": request.build_absolute_uri("/api/v1/"),
                "docs_url": request.build_absolute_uri("/api/docs/"),
                "health_url": request.build_absolute_uri("/api/health/live/"),
            }
        )
        return context


@extend_schema(tags=["System"], responses={200: API_ROOT_RESPONSE})
class APIRootView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        base = request.build_absolute_uri("/").rstrip("/")
        routes = {
            "health": f"{base}/api/health/live/",
            "readiness": f"{base}/api/health/ready/",
            "schema": f"{base}/api/schema/",
            "documentation": f"{base}/api/docs/",
            "auth": f"{base}/api/v1/auth/",
            "profile": f"{base}/api/v1/students/profile/",
            "subjects": f"{base}/api/v1/subjects/",
            "study_plans": f"{base}/api/v1/study-plans/",
            "quizzes": f"{base}/api/v1/quizzes/",
            "sources": f"{base}/api/v1/student-sources/",
            "ai": f"{base}/api/v1/ai/jobs/",
            "subscriptions": f"{base}/api/v1/subscriptions/",
            "notifications": f"{base}/api/v1/notifications/",
            "support": f"{base}/api/v1/support/tickets/",
            "dashboard": f"{base}/api/v1/admin/",
        }
        return success_response(
            data={
                "name": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "api_version": settings.API_VERSION,
                "routes": routes,
            },
            message="Baraq API is available.",
        )
