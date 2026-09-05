from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import render


def _wants_json(request):
    return request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", "")


def handler404(request, exception=None):
    if _wants_json(request):
        return JsonResponse(
            {
                "success": False,
                "message": "المسار المطلوب غير موجود.",
                "code": "not_found",
                "errors": {},
                "request_id": getattr(request, "request_id", None),
            },
            status=404,
        )
    return render(request, "404.html", status=404)


def handler500(request):
    if _wants_json(request):
        return JsonResponse(
            {
                "success": False,
                "message": "حدث خطأ داخلي غير متوقع.",
                "code": "server_error",
                "errors": {},
                "request_id": getattr(request, "request_id", None),
            },
            status=500,
        )
    return render(request, "500.html", status=500)
