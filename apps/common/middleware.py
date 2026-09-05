from __future__ import annotations

import re
import time
import uuid

from django.conf import settings

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


class RequestIDMiddleware:
    """Attach a stable request id to every request and response."""

    header_name = "HTTP_X_REQUEST_ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get(self.header_name, "")
        request.request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        started = time.monotonic()
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        response["X-Response-Time-Ms"] = str(round((time.monotonic() - started) * 1000, 2))
        return response


class APIVersionHeadersMiddleware:
    """Publish the canonical API version and mark legacy API routes."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response["X-API-Version"] = settings.API_VERSION
        if request.path.startswith("/api/") and not request.path.startswith(
            ("/api/v1/", "/api/schema/", "/api/docs/", "/api/redoc/", "/api/health/", "/api/meta/", "/api/internal/")
        ):
            response["Deprecation"] = "true"
            response["Sunset"] = settings.LEGACY_API_SUNSET
            response["Link"] = '</api/v1/>; rel="successor-version"'
        return response
