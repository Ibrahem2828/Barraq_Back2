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
    SOURCE_DOWNLOAD_FAILED = "source_download_failed", "Source download failed"
    SOURCE_CHECKSUM_MISMATCH = "source_checksum_mismatch", "Source checksum mismatch"
    SOURCE_VERSION_CHANGED = "source_version_changed", "Source version changed"
    SOURCE_INGESTION_FAILED = "source_ingestion_failed", "Source ingestion failed"
    UNSUPPORTED_SOURCE_FORMAT = "unsupported_source_format", "Unsupported source format"
    PDF_OCR_REQUIRED = "pdf_ocr_required", "PDF requires OCR"
    EMBEDDING_FAILED = "embedding_failed", "Embedding failed"
    RETRIEVAL_FAILED = "retrieval_failed", "Retrieval failed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict", "Idempotency key conflict"
    PROVIDER_RATE_LIMITED = "provider_rate_limited", "Provider rate limited"
    PROVIDER_TIMEOUT = "provider_timeout", "Provider timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable", "Provider unavailable"
    VALIDATION_FAILED = "validation_failed", "Output validation failed"
    RESULT_VALIDATION_FAILED = "result_validation_failed", "Result validation failed"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed", "Output validation failed"
    WORKER_INTERRUPTED = (
        "worker_interrupted_execution_uncertain",
        "Worker interrupted after processing began",
    )

    @classmethod
    def values_set(cls) -> frozenset[str]:
        return frozenset(choice.value for choice in cls)


# Codes the AI service itself is authorized to return in a job/webhook error
# body (Baraq_AI's app.core.errors.ErrorCategory) -- if the upstream response
# names one of these directly, Django propagates it verbatim instead of
# re-guessing from the HTTP status code alone.
_KNOWN_UPSTREAM_CODES = ErrorCode.values_set()

_PUBLIC_ERROR_MESSAGES: dict[str, str] = {
    ErrorCode.PDF_OCR_REQUIRED: "This PDF does not contain extractable text and requires OCR.",
    ErrorCode.UNSUPPORTED_SOURCE_FORMAT: "This source format is not supported.",
    ErrorCode.SOURCE_NOT_FOUND: "The selected source is no longer available.",
    ErrorCode.SOURCE_FORBIDDEN: "The selected source is not available for this request.",
    ErrorCode.SOURCE_DOWNLOAD_FAILED: "The source could not be downloaded.",
    ErrorCode.SOURCE_CHECKSUM_MISMATCH: "The downloaded source did not match the requested version.",
    ErrorCode.SOURCE_VERSION_CHANGED: "The source changed after this job was created.",
    ErrorCode.SOURCE_INGESTION_FAILED: "The source could not be prepared for AI use.",
    ErrorCode.EMBEDDING_FAILED: "The source could not be indexed.",
    ErrorCode.RETRIEVAL_FAILED: "Relevant source material could not be retrieved.",
    ErrorCode.PROVIDER_TIMEOUT: "The AI provider timed out.",
    ErrorCode.PROVIDER_RATE_LIMITED: "The AI provider is temporarily rate limited.",
    ErrorCode.PROVIDER_UNAVAILABLE: "The AI provider is temporarily unavailable.",
    ErrorCode.VALIDATION_FAILED: "The generated result did not pass validation.",
    ErrorCode.RESULT_VALIDATION_FAILED: "The generated result did not pass validation.",
    ErrorCode.OUTPUT_VALIDATION_FAILED: "The generated result did not pass validation.",
    ErrorCode.WORKER_INTERRUPTED: "The job stopped safely after a worker interruption.",
}

_RETRYABLE_CODES: frozenset[str] = frozenset(
    {
        ErrorCode.SOURCE_DOWNLOAD_FAILED,
        ErrorCode.SOURCE_INGESTION_FAILED,
        ErrorCode.EMBEDDING_FAILED,
        ErrorCode.RETRIEVAL_FAILED,
        ErrorCode.PROVIDER_TIMEOUT,
        ErrorCode.PROVIDER_RATE_LIMITED,
        ErrorCode.PROVIDER_UNAVAILABLE,
    }
)


def public_error_message(code: str) -> str:
    """Return fixed safe text; never forward an upstream diagnostic."""

    return _PUBLIC_ERROR_MESSAGES.get(
        code, "The AI request could not be completed. Please try again."
    )


def is_retryable_error(code: str) -> bool:
    return code in _RETRYABLE_CODES

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
