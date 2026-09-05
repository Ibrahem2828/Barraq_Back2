"""The single authenticated internal-call protocol for Baraq services.

This module intentionally has no HMAC V1 fallback. Every Django -> AI and
AI -> Django request is bound to its service identity, request target, body,
timestamp, key id, and a single-use nonce.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from django.conf import settings
from django.core.cache import cache
from rest_framework.permissions import BasePermission

HMAC_V2_SCHEME = "BARAQ-HMAC-V2"
REQUIRED_HMAC_HEADERS = (
    "x-baraq-service",
    "x-baraq-key-id",
    "x-baraq-timestamp",
    "x-baraq-nonce",
    "x-content-sha256",
    "x-baraq-signature",
)


class InternalAuthenticationError(ValueError):
    """A safe, stable reason suitable for internal metrics and tests."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AuthenticatedService:
    service: str
    key_id: str
    nonce: str


def canonical_request_target(target: str) -> str:
    """Return the RFC 3986 canonical relative request target for HMAC V2."""

    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        raise ValueError("Internal request targets must be relative absolute paths.")
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    if not query_pairs:
        return parsed.path
    canonical_query = urlencode(
        sorted(query_pairs), doseq=True, quote_via=quote, safe="-._~"
    )
    return f"{parsed.path}?{canonical_query}"


def canonical_request(
    *,
    service: str,
    key_id: str,
    timestamp: int,
    nonce: str,
    method: str,
    target: str,
    body_sha256: str,
) -> bytes:
    return "\n".join(
        (
            HMAC_V2_SCHEME,
            service,
            key_id,
            str(timestamp),
            nonce,
            method.upper(),
            canonical_request_target(target),
            body_sha256,
        )
    ).encode("utf-8")


def _hmac_keyring() -> dict[str, str]:
    try:
        parsed = json.loads(settings.BARAQ_HMAC_KEYS_JSON)
    except (TypeError, json.JSONDecodeError) as exc:
        raise InternalAuthenticationError("invalid_keyring") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise InternalAuthenticationError("invalid_keyring")
    if any(
        not isinstance(key_id, str)
        or not key_id
        or not isinstance(secret, str)
        or not secret
        for key_id, secret in parsed.items()
    ):
        raise InternalAuthenticationError("invalid_keyring")
    return parsed


def _is_lower_hex(value: str) -> bool:
    return len(value) == 64 and value == value.lower() and all(
        character in "0123456789abcdef" for character in value
    )


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _verify_hmac_v2(
    *, method: str, target: str, body: bytes, headers: Mapping[str, str], now: int | None = None
) -> AuthenticatedService:
    normalized = _normalized_headers(headers)
    try:
        service, key_id, raw_timestamp, nonce, received_hash, received_signature = (
            normalized[header] for header in REQUIRED_HMAC_HEADERS
        )
    except KeyError as exc:
        raise InternalAuthenticationError("missing_signature_headers") from exc

    if service not in settings.BARAQ_HMAC_ALLOWED_SERVICES:
        raise InternalAuthenticationError("unauthorized_service")
    try:
        timestamp = int(raw_timestamp)
    except (TypeError, ValueError) as exc:
        raise InternalAuthenticationError("invalid_timestamp") from exc
    if len(nonce) < 16 or len(nonce) > 128:
        raise InternalAuthenticationError("invalid_nonce")
    if not _is_lower_hex(received_hash):
        raise InternalAuthenticationError("invalid_content_hash")
    expected_hash = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise InternalAuthenticationError("invalid_content_hash")
    if abs((int(time.time()) if now is None else now) - timestamp) > settings.BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS:
        raise InternalAuthenticationError("expired_signature")
    if not _is_lower_hex(received_signature):
        raise InternalAuthenticationError("invalid_signature")
    keyring = _hmac_keyring()
    secret = keyring.get(key_id)
    if secret is None:
        raise InternalAuthenticationError("unknown_key_id")
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        canonical_request(
            service=service,
            key_id=key_id,
            timestamp=timestamp,
            nonce=nonce,
            method=method,
            target=target,
            body_sha256=expected_hash,
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_signature, expected_signature):
        raise InternalAuthenticationError("invalid_signature")
    return AuthenticatedService(service=service, key_id=key_id, nonce=nonce)


def verify_internal_request(request, *, body: bytes | None = None) -> AuthenticatedService:
    """Verify a request and atomically reserve its nonce in the shared cache."""

    raw_body = request.body if body is None else body
    authenticated = _verify_hmac_v2(
        method=request.method,
        target=request.get_full_path(),
        body=raw_body,
        headers=request.headers,
    )
    cache_key = f"baraq:hmac:nonce:{authenticated.service}:{authenticated.nonce}"
    try:
        accepted = cache.add(cache_key, "1", timeout=settings.BARAQ_HMAC_NONCE_TTL_SECONDS)
    except Exception as exc:  # Fail closed if the replay store is unavailable.
        raise InternalAuthenticationError("replay_store_unavailable") from exc
    if not accepted:
        raise InternalAuthenticationError("replay_detected")
    return authenticated


def make_service_signature(
    *,
    method: str,
    target: str,
    body: bytes,
    timestamp: int | None = None,
    nonce: str | None = None,
    service: str | None = None,
    key_id: str | None = None,
) -> dict[str, str]:
    """Sign a Django-originated internal request with the HMAC V2 keyring."""

    service = service or settings.BARAQ_SERVICE_ID
    key_id = key_id or settings.BARAQ_HMAC_CURRENT_KEY_ID
    timestamp = int(time.time()) if timestamp is None else timestamp
    nonce = nonce or secrets.token_urlsafe(24)
    if len(nonce) < 16 or len(nonce) > 128:
        raise ValueError("HMAC nonce must be between 16 and 128 characters.")
    secret = _hmac_keyring().get(key_id)
    if secret is None:
        raise ValueError("The configured HMAC key id is not in the keyring.")
    body_sha256 = hashlib.sha256(body).hexdigest()
    signature = hmac.new(
        secret.encode("utf-8"),
        canonical_request(
            service=service,
            key_id=key_id,
            timestamp=timestamp,
            nonce=nonce,
            method=method,
            target=target,
            body_sha256=body_sha256,
        ),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Baraq-Service": service,
        "X-Baraq-Key-Id": key_id,
        "X-Baraq-Timestamp": str(timestamp),
        "X-Baraq-Nonce": nonce,
        "X-Content-SHA256": body_sha256,
        "X-Baraq-Signature": signature,
    }


class HasInternalServiceKey(BasePermission):
    """HMAC V2 permission retained under its old import name for route stability."""

    message = "Invalid internal service signature."

    def has_permission(self, request, view):
        try:
            verify_internal_request(request)
        except InternalAuthenticationError:
            return False
        return True


def verify_webhook(request, body: bytes | None = None) -> bool:
    """Verify an AI callback using exactly the same HMAC V2 protocol."""

    try:
        verify_internal_request(request, body=body)
    except InternalAuthenticationError:
        return False
    return True
