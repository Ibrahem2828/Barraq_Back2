from __future__ import annotations

from rest_framework.renderers import JSONRenderer


class EnvelopeJSONRenderer(JSONRenderer):
    """Return a stable response envelope for mobile, dashboard, and service clients."""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        context = renderer_context or {}
        response = context.get("response")
        request = context.get("request")
        status_code = getattr(response, "status_code", 200)

        if data is None:
            payload = None
        elif isinstance(data, dict) and "success" in data:
            payload = data
        elif status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            payload = {
                "success": False,
                "message": str(detail or "Request failed"),
                "code": "request_error",
                "errors": data,
            }
        elif isinstance(data, dict) and {"count", "results"}.issubset(data):
            payload = {
                "success": True,
                "message": "Success",
                "data": data.get("results", []),
                "meta": {
                    "count": data.get("count", 0),
                    "next": data.get("next"),
                    "previous": data.get("previous"),
                },
            }
        else:
            payload = {"success": True, "message": "Success", "data": data}

        if isinstance(payload, dict) and request is not None:
            payload.setdefault("request_id", getattr(request, "request_id", None))
        return super().render(payload, accepted_media_type, context)
