"""Pure, unit-testable helpers for resolving environment-driven settings.

Extracted out of ``config/settings.py`` so a renamed or duplicated env var
(e.g. a stray ``DJANGO_CSRF_TRUSTED_ORIGINS`` next to the real
``CSRF_TRUSTED_ORIGINS``) fails a fast, explicit test instead of silently
falling back to a wrong default in production.
"""
from __future__ import annotations

import math
from collections import Counter
from urllib.parse import urlsplit


def resolve_list_setting(env, primary_key, *, fallback_keys=(), default=None):
    """Read a comma-separated list setting from ``primary_key``.

    If ``primary_key`` is unset or empty, raise on any of ``fallback_keys``
    being set instead of silently ignoring them -- a same-purpose variable
    under the wrong name is a configuration bug, not a fallback source.
    """
    value = env.list(primary_key, default=[])
    if value:
        stray = [key for key in fallback_keys if env.str(key, default="")]
        if stray:
            raise ValueError(
                f"{primary_key} is set, but so is {stray[0]!r}, which is not read by "
                "this application. Merge its values into "
                f"{primary_key} and remove {stray[0]!r} to avoid silent misconfiguration."
            )
        return value

    for key in fallback_keys:
        if env.str(key, default=""):
            raise ValueError(
                f"{key!r} is set but is not a recognized setting name. "
                f"Rename it to {primary_key!r}."
            )

    return list(default or [])


def validate_secret_key(value, *, minimum_length=50):
    """Reject production Django secrets that are clearly weak or placeholders."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("SECRET_KEY is missing from the production environment.")
    value = value.strip()
    if len(value) < minimum_length:
        raise ValueError(f"SECRET_KEY must be at least {minimum_length} characters long.")
    if value.startswith("django-insecure"):
        raise ValueError("SECRET_KEY must not use Django's insecure development prefix.")
    unique_ratio = len(set(value)) / len(value)
    if unique_ratio < 0.5:
        raise ValueError("SECRET_KEY has insufficient character variety.")
    counts = Counter(value)
    entropy = -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())
    if entropy < 4.0:
        raise ValueError("SECRET_KEY has insufficient entropy.")


def validate_origin_url(value, *, setting_name, require_https):
    """Validate a browser or public API origin without echoing its value."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{setting_name} must not be empty.")
    parsed = urlsplit(value)
    allowed_schemes = {"https"} if require_https else {"http", "https"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        scheme_requirement = "an HTTPS" if require_https else "an HTTP or HTTPS"
        raise ValueError(f"{setting_name} must be {scheme_requirement} origin with a hostname.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError(f"{setting_name} must be an origin only, without credentials, path, query, or fragment.")
    return parsed.hostname.lower()


def validate_origin_list(values, *, setting_name, require_https):
    """Validate every configured CORS or CSRF origin."""

    for value in values:
        validate_origin_url(value, setting_name=setting_name, require_https=require_https)


def validate_allowed_hosts(values, *, public_api_hostname=None):
    """Reject unsafe host configuration and require the public API hostname."""

    normalized = [str(value).strip().lower().rstrip(".") for value in values if str(value).strip()]
    if not normalized:
        raise ValueError("ALLOWED_HOSTS must be configured in production.")
    if "*" in normalized:
        raise ValueError("ALLOWED_HOSTS must not contain '*' in production.")
    if public_api_hostname:
        host = public_api_hostname.lower().rstrip(".")
        is_covered = host in normalized or any(
            candidate.startswith(".") and host.endswith(candidate) for candidate in normalized
        )
        if not is_covered:
            raise ValueError("ALLOWED_HOSTS must include the PUBLIC_API_BASE_URL hostname.")
