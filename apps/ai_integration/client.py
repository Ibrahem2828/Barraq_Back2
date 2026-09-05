from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter

from .error_codes import map_upstream_error_code
from .security import make_service_signature

logger = logging.getLogger(__name__)

# Shared across every AIServiceClient instance in this worker process so the
# underlying TCP/TLS connections to the AI service are kept warm and reused
# (keep-alive) instead of being renegotiated on every dispatched job. This is
# a pure connection-pooling optimization: max_retries=0 preserves the exact
# retry/error semantics the caller already implements (Celery task retry +
# the outbox reconciliation sweep), so nothing here retries a request twice.
_POOL_ADAPTER = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)


def _build_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", _POOL_ADAPTER)
    session.mount("http://", _POOL_ADAPTER)
    session.headers.update({"Accept": "application/json", "User-Agent": "Baraq-Backend/1.0"})
    return session


_SHARED_SESSION = _build_session()


class AIServiceError(RuntimeError):
    def __init__(self, message, *, code="ai_service_error", status_code=None, retryable=False):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class AIServiceResponse:
    data: dict
    status_code: int


class AIServiceClient:
    def __init__(self):
        self.base_url = settings.AI_SERVICE_BASE_URL.rstrip("/") + "/"
        self.timeout = settings.AI_SERVICE_TIMEOUT_SECONDS
        self.session = _SHARED_SESSION

    def _request(self, method, path, *, payload=None, idempotency_key=None):
        if not settings.AI_SERVICE_ENABLED:
            raise AIServiceError("AI service is disabled.", code="ai_service_disabled", status_code=503)
        method = method.upper()
        target = "/" + path.lstrip("/")
        body = b"" if payload is None else json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        headers = make_service_signature(method=method, target=target, body=body)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        url = urljoin(self.base_url, target.lstrip("/"))
        try:
            response = self.session.request(
                method,
                url,
                data=body or None,
                headers=headers,
                timeout=self.timeout,
                verify=settings.AI_SERVICE_VERIFY_SSL,
                # A redirect can forward the internal API key and HMAC
                # signature to a different origin. The service base URL is a
                # configured trust boundary, so redirects are never valid.
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise AIServiceError("AI service timeout.", code="ai_service_timeout", retryable=True) from exc
        except requests.RequestException as exc:
            raise AIServiceError("AI service is unavailable.", code="ai_service_unavailable", retryable=True) from exc
        if response.status_code >= 300:
            retryable = response.status_code in {408, 425, 429, 500, 502, 503, 504}
            try:
                data = response.json()
            except ValueError:
                data = {}
            # The AI service's error envelope nests its code under "error",
            # e.g. {"success": false, "error": {"code": "...", ...}} -- not
            # at the top level.
            remote_error = data.get("error") if isinstance(data.get("error"), dict) else {}
            remote_code = str(remote_error.get("code") or "")[:80] or None
            logger.warning(
                "AI service request failed with status=%s remote_code=%s",
                response.status_code,
                remote_code or "unknown",
            )
            code = map_upstream_error_code(status_code=response.status_code, remote_code=remote_code)
            raise AIServiceError(
                "AI service is unavailable." if retryable else "AI service rejected the request.",
                code=code,
                status_code=response.status_code,
                retryable=retryable,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise AIServiceError(
                "AI service returned an invalid response.",
                code="invalid_ai_service_response",
                status_code=response.status_code,
                retryable=False,
            ) from exc
        return AIServiceResponse(data=data.get("data", data), status_code=response.status_code)

    def create_job(self, payload):
        return self._request("POST", settings.AI_SERVICE_JOBS_PATH, payload=payload)

    def get_job(self, external_job_id):
        return self._request("GET", f"{settings.AI_SERVICE_JOBS_PATH.rstrip('/')}/{external_job_id}")

    def cancel_job(self, external_job_id):
        return self._request("POST", f"{settings.AI_SERVICE_JOBS_PATH.rstrip('/')}/{external_job_id}/cancel", payload={})

    def send_feedback(self, payload, idempotency_key):
        return self._request("POST", settings.AI_SERVICE_FEEDBACK_PATH, payload=payload, idempotency_key=idempotency_key)

    def health(self):
        return self._request("GET", settings.AI_SERVICE_HEALTH_PATH)
