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
    SOURCE_PROJECT_MISMATCH = "source_project_mismatch", "Source project mismatch"
    SOURCE_SIZE_MISMATCH = "source_size_mismatch", "Source size mismatch"
    SOURCE_TOO_LARGE = "source_too_large", "Source too large"
    SOURCE_CHECKSUM_MISMATCH = "source_checksum_mismatch", "Source checksum mismatch"
    SOURCE_VERSION_CHANGED = "source_version_changed", "Source version changed"
    SOURCE_INGESTION_FAILED = "source_ingestion_failed", "Source ingestion failed"
    UNSUPPORTED_SOURCE_FORMAT = "unsupported_source_format", "Unsupported source format"
    PDF_OCR_REQUIRED = "pdf_ocr_required", "PDF requires OCR"
    TEXT_DECODE_FAILED = "text_decode_failed", "Text decoding failed"
    PDF_READ_FAILED = "pdf_read_failed", "PDF extraction failed"
    DOCX_READ_FAILED = "docx_read_failed", "DOCX extraction failed"
    PPTX_READ_FAILED = "pptx_read_failed", "PPTX extraction failed"
    EMPTY_SOURCE = "empty_source", "Source contains no readable text"
    EMPTY_CHUNKS = "empty_chunks", "Source contains no useful chunks"
    EMBEDDING_FAILED = "embedding_failed", "Embedding failed"
    EMBEDDING_COUNT_MISMATCH = "embedding_count_mismatch", "Embedding count mismatch"
    RETRIEVAL_FAILED = "retrieval_failed", "Retrieval failed"
    INSUFFICIENT_SOURCE_CONTEXT = "insufficient_source_context", "Insufficient source context"
    MISSING_AUTHORITATIVE_DATA = "missing_authoritative_data", "Missing authoritative data"
    KHOTA_NO_STUDY_DAYS = "khota_no_study_days", "No study days available"
    KHOTA_CONSTRAINT_VIOLATION = "khota_constraint_violation", "Study plan constraints invalid"
    AUDIO_SOURCE_REQUIRED = "audio_source_required", "Audio source required"
    AUDIO_TOO_LARGE = "audio_too_large", "Audio source too large"
    EMPTY_TRANSCRIPTION = "empty_transcription", "Transcription is empty"
    TRANSCRIPTION_FAILED = "transcription_failed", "Transcription failed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict", "Idempotency key conflict"
    PROVIDER_RATE_LIMITED = "provider_rate_limited", "Provider rate limited"
    PROVIDER_TIMEOUT = "provider_timeout", "Provider timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable", "Provider unavailable"
    ALL_PROVIDERS_FAILED = "all_providers_failed", "All providers failed"
    NO_PROVIDER_AVAILABLE = "no_provider_available", "No provider available"
    VALIDATION_FAILED = "validation_failed", "Output validation failed"
    RESULT_VALIDATION_FAILED = "result_validation_failed", "Result validation failed"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed", "Output validation failed"
    # The AI service's grounding checks (Baraq_AI app/rag/grounding.py): the
    # generated result could not be verified against the learner's sources.
    # They were unknown here and surfaced as "provider unavailable".
    UNSUPPORTED_CLAIM = "unsupported_claim", "Claim not supported by its evidence"
    UNVERIFIABLE_CLAIM = "unverifiable_claim", "Claim has no verifiable content"
    INVALID_SOURCE_REFERENCE = "invalid_source_reference", "Invalid source reference"
    UNSUPPORTED_TOPIC_REFERENCE = "unsupported_topic_reference", "Unsupported topic reference"
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
    ErrorCode.SOURCE_PROJECT_MISMATCH: "The selected source is not part of this project.",
    ErrorCode.SOURCE_SIZE_MISMATCH: "The downloaded source size did not match its manifest.",
    ErrorCode.SOURCE_TOO_LARGE: "The source exceeds the AI processing limit.",
    ErrorCode.SOURCE_CHECKSUM_MISMATCH: "The downloaded source did not match the requested version.",
    ErrorCode.SOURCE_VERSION_CHANGED: "The source changed after this job was created.",
    ErrorCode.SOURCE_INGESTION_FAILED: "The source could not be prepared for AI use.",
    ErrorCode.TEXT_DECODE_FAILED: "The text source could not be decoded.",
    ErrorCode.PDF_READ_FAILED: "The PDF could not be read.",
    ErrorCode.DOCX_READ_FAILED: "The DOCX document could not be read.",
    ErrorCode.PPTX_READ_FAILED: "The PPTX presentation could not be read.",
    ErrorCode.EMPTY_SOURCE: "The source does not contain readable text.",
    ErrorCode.EMPTY_CHUNKS: "The source does not contain enough usable text.",
    ErrorCode.EMBEDDING_FAILED: "The source could not be indexed.",
    ErrorCode.EMBEDDING_COUNT_MISMATCH: "The source index could not be verified.",
    ErrorCode.RETRIEVAL_FAILED: "Relevant source material could not be retrieved.",
    ErrorCode.INSUFFICIENT_SOURCE_CONTEXT: "The selected sources do not contain enough relevant material.",
    ErrorCode.MISSING_AUTHORITATIVE_DATA: "There is not enough learner performance data for this analysis.",
    ErrorCode.KHOTA_NO_STUDY_DAYS: "No available study days remain in the selected period.",
    ErrorCode.KHOTA_CONSTRAINT_VIOLATION: "The requested study-plan constraints cannot be satisfied.",
    ErrorCode.AUDIO_SOURCE_REQUIRED: "Sada requires a supported audio source.",
    ErrorCode.AUDIO_TOO_LARGE: "The audio source exceeds the transcription limit.",
    ErrorCode.EMPTY_TRANSCRIPTION: "No speech could be transcribed from this audio source.",
    ErrorCode.TRANSCRIPTION_FAILED: "The audio could not be transcribed.",
    ErrorCode.PROVIDER_TIMEOUT: "The AI provider timed out.",
    ErrorCode.PROVIDER_RATE_LIMITED: "The AI provider is temporarily rate limited.",
    ErrorCode.PROVIDER_UNAVAILABLE: "The AI provider is temporarily unavailable.",
    ErrorCode.ALL_PROVIDERS_FAILED: "The AI provider is temporarily unavailable.",
    ErrorCode.NO_PROVIDER_AVAILABLE: "No AI provider is currently available for this request.",
    ErrorCode.VALIDATION_FAILED: "The generated result did not pass validation.",
    ErrorCode.RESULT_VALIDATION_FAILED: "The generated result did not pass validation.",
    ErrorCode.OUTPUT_VALIDATION_FAILED: "The generated result did not pass validation.",
    ErrorCode.UNSUPPORTED_CLAIM: "The result could not be verified against your sources. Please try again.",
    ErrorCode.UNVERIFIABLE_CLAIM: "The result could not be verified against your sources. Please try again.",
    ErrorCode.INVALID_SOURCE_REFERENCE: "The result could not be verified against your sources. Please try again.",
    ErrorCode.UNSUPPORTED_TOPIC_REFERENCE: "The result could not be verified against your data. Please try again.",
    ErrorCode.WORKER_INTERRUPTED: "The job stopped safely after a worker interruption.",
}

_RETRYABLE_CODES: frozenset[str] = frozenset(
    {
        ErrorCode.SOURCE_DOWNLOAD_FAILED,
        ErrorCode.SOURCE_INGESTION_FAILED,
        ErrorCode.EMBEDDING_FAILED,
        ErrorCode.RETRIEVAL_FAILED,
        ErrorCode.TRANSCRIPTION_FAILED,
        ErrorCode.PROVIDER_TIMEOUT,
        ErrorCode.PROVIDER_RATE_LIMITED,
        ErrorCode.PROVIDER_UNAVAILABLE,
        ErrorCode.ALL_PROVIDERS_FAILED,
        # A new generation is likely to pass: the check rejects one output.
        ErrorCode.UNSUPPORTED_CLAIM,
        ErrorCode.UNVERIFIABLE_CLAIM,
        ErrorCode.INVALID_SOURCE_REFERENCE,
        ErrorCode.UNSUPPORTED_TOPIC_REFERENCE,
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
