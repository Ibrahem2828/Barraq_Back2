"""Normalized error-code vocabulary for the Django <-> Baraq AI integration
(spec section 27: "error.code ثابت لا نص الرسالة"). ``AIJob.error_code`` and
every internal-auth/upstream error mapping in this app must use one of
these values so the AI service, dashboard, and mobile client can branch on
a stable code rather than a translatable message string.
"""

from __future__ import annotations

from django.db import models


class ErrorCode(models.TextChoices):
    INVALID_CONTRACT = "invalid_contract", "Invalid contract"
    INVALID_TASK_INPUT = "invalid_task_input", "Invalid task input"
    INVALID_SIGNATURE = "invalid_signature", "Invalid HMAC signature"
    REPLAY_DETECTED = "replay_detected", "Replayed nonce"
    SOURCE_FORBIDDEN = "source_forbidden", "Source ownership rejected"
    SOURCE_NOT_FOUND = "source_not_found", "Source not found"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict", "Idempotency key conflict"
    PROVIDER_RATE_LIMITED = "provider_rate_limited", "Provider rate limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable", "Provider unavailable"
    VALIDATION_FAILED = "validation_failed", "Output validation failed"

    @classmethod
    def values_set(cls) -> frozenset[str]:
        return frozenset(choice.value for choice in cls)


# Codes the AI service itself is authorized to return in a job/webhook error
# body (Baraq_AI's app.core.errors.ErrorCategory) -- if the upstream response
# names one of these directly, Django propagates it verbatim instead of
# re-guessing from the HTTP status code alone.
_KNOWN_UPSTREAM_CODES = ErrorCode.values_set()

# HTTP statuses that are transient by nature, mirroring Baraq_AI's own
# app.core.errors.classify_provider_status taxonomy so both sides agree on
# what "retryable" means.
_RATE_LIMIT_STATUS = 429
_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def map_upstream_error_code(*, status_code: int | None, remote_code: str | None) -> str:
    """Map an AI-service HTTP failure to this app's error-code vocabulary."""

    if remote_code in _KNOWN_UPSTREAM_CODES:
        return remote_code
    if status_code == _RATE_LIMIT_STATUS:
        return ErrorCode.PROVIDER_RATE_LIMITED
    if status_code in _RETRYABLE_STATUSES:
        return ErrorCode.PROVIDER_UNAVAILABLE
    # A non-retryable 4xx the AI service didn't label with a known code is
    # treated as a contract problem on our side rather than invented meaning.
    return ErrorCode.INVALID_CONTRACT
