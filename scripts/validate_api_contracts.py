#!/usr/bin/env python3
"""Fail CI when Baraq client API contracts drift from Django routing.

Run ``generate_api_contracts.py --check`` first in CI.  This validator adds
structural, audience-boundary, route/method and source-traceability checks
that are intentionally independent from the generator's file comparison.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = ROOT / "contracts"
CONTRACT_FILES = {
    "mobile": CONTRACTS_DIR / "mobile_api_contract.json",
    "dashboard": CONTRACTS_DIR / "dashboard_api_contract.json",
}
OPENAPI_FILE = CONTRACTS_DIR / "openapi.json"
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
RESOURCE_REF = re.compile(r"^#/resources/([^/]+)/schema$")
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
DASHBOARD_SHARED_PATHS = {
    "/api/v1/auth/login/",
    "/api/v1/auth/refresh/",
    "/api/v1/auth/verify/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/change-password/",
    "/api/v1/auth/password-reset/",
    "/api/v1/auth/password-reset/confirm/",
    "/api/v1/ai/service-health/",
}
MOBILE_FORBIDDEN_PREFIXES = ("/api/v1/admin/", "/api/internal/")
MOBILE_FORBIDDEN_PATHS = {"/api/v1/ai/service-health/"}

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"{path.relative_to(ROOT)} is not valid UTF-8 JSON: {exc}")
        return None
    if not isinstance(value, dict):
        fail(errors, f"{path.relative_to(ROOT)} root must be a JSON object.")
        return None
    return value


def sample_path(path: str) -> str:
    values = {"public_id": "00000000-0000-0000-0000-000000000000", "id": "1", "pk": "1"}
    return re.sub(r"\{([^}]+)\}", lambda match: values.get(match.group(1), "1"), path)


def resolved_view(path: str) -> tuple[str | None, set[str]]:
    from django.urls import resolve

    match = resolve(sample_path(path))
    callback = match.func
    view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    view_name = None
    if view_class is not None:
        view_name = f"{view_class.__module__}.{view_class.__name__}"
    actions = {method.upper() for method in getattr(callback, "actions", {})}
    if not actions and view_class is not None:
        actions = {method.upper() for method in getattr(view_class, "http_method_names", [])}
    return view_name, actions


def collect_resource_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            match = RESOURCE_REF.match(ref)
            if match:
                refs.add(match.group(1))
        for item in value.values():
            refs.update(collect_resource_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(collect_resource_refs(item))
    return refs


def sensitive_strings(value: Any) -> list[str]:
    found: list[str] = []
    patterns = (
        re.compile(r"\b(?:sk|rk|pk)_[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"\bAIza[\w-]{20,}\b"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"postgres(?:ql)?://[^\s:/]+:[^\s@]+@", re.IGNORECASE),
    )
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in patterns):
            found.append(value[:80])
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(sensitive_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(sensitive_strings(item))
    return found


def validate_contract(
    audience: str,
    contract: dict[str, Any],
    openapi: dict[str, Any],
    errors: list[str],
) -> None:
    metadata = contract.get("contract")
    if not isinstance(metadata, dict):
        fail(errors, f"{audience}: missing contract metadata object.")
        return
    if metadata.get("audience") != audience:
        fail(errors, f"{audience}: contract.audience must equal {audience!r}.")
    if not SEMVER.fullmatch(str(metadata.get("version", ""))):
        fail(errors, f"{audience}: contract.version must be semantic versioning.")
    for key in ("generated_from_backend_commit", "generated_at", "compatible_backend_contract_version"):
        if not metadata.get(key):
            fail(errors, f"{audience}: contract.{key} is required.")
    if metadata.get("api_base_url_env") != "PUBLIC_API_BASE_URL":
        fail(errors, f"{audience}: api_base_url_env must be PUBLIC_API_BASE_URL.")

    endpoints = contract.get("endpoints")
    if not isinstance(endpoints, list):
        fail(errors, f"{audience}: endpoints must be an array.")
        return
    identifiers: set[str] = set()
    method_paths: set[tuple[str, str]] = set()
    resources = contract.get("resources", {})
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            fail(errors, f"{audience}: endpoint is not an object.")
            continue
        identifier = endpoint.get("id")
        method = endpoint.get("method")
        path = endpoint.get("path")
        if not isinstance(identifier, str) or not identifier:
            fail(errors, f"{audience}: endpoint without a stable id.")
        elif identifier in identifiers:
            fail(errors, f"{audience}: duplicate endpoint id {identifier!r}.")
        else:
            identifiers.add(identifier)
        if method not in HTTP_METHODS or not isinstance(path, str):
            fail(errors, f"{audience}: invalid method/path for {identifier!r}.")
            continue
        pair = (method, path)
        if pair in method_paths:
            fail(errors, f"{audience}: duplicate method/path {method} {path}.")
        method_paths.add(pair)
        if not path.startswith("/api/v1/"):
            fail(errors, f"{audience}: non-canonical path {path}.")
        if audience == "mobile" and (
            path.startswith(MOBILE_FORBIDDEN_PREFIXES) or path in MOBILE_FORBIDDEN_PATHS
        ):
            fail(errors, f"mobile: privileged/internal endpoint leaked into contract: {path}.")
        if audience == "dashboard" and not (
            path.startswith("/api/v1/admin/") or path in DASHBOARD_SHARED_PATHS
        ):
            fail(errors, f"dashboard: unapproved non-dashboard path {path}.")
        if path not in openapi.get("paths", {}) or method.lower() not in openapi["paths"].get(path, {}):
            fail(errors, f"{audience}: {method} {path} is absent from generated OpenAPI.")
        try:
            source_view, allowed_methods = resolved_view(path)
        except Exception as exc:  # noqa: BLE001 -- route errors are CI failures, not user input.
            fail(errors, f"{audience}: Django cannot resolve {path}: {exc}")
            continue
        if method not in allowed_methods:
            fail(errors, f"{audience}: Django route does not allow {method} {path}.")
        backend_source = endpoint.get("backend_source", {})
        if backend_source.get("view") != source_view:
            fail(errors, f"{audience}: backend source view drift for {method} {path}.")
        for resource in collect_resource_refs(endpoint):
            if resource not in resources:
                fail(errors, f"{audience}: {identifier} references unknown resource {resource}.")

    for resource_name, resource in resources.items():
        for reference in collect_resource_refs(resource):
            if reference not in resources:
                fail(errors, f"{audience}: resource {resource_name} references unknown resource {reference}.")
    leaked_secrets = sensitive_strings(contract)
    if leaked_secrets:
        fail(errors, f"{audience}: possible secret material detected: {leaked_secrets!r}")


def main() -> int:
    errors: list[str] = []
    documents = {audience: load_json(path, errors) for audience, path in CONTRACT_FILES.items()}
    openapi = load_json(OPENAPI_FILE, errors)
    if errors or openapi is None:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    for audience, document in documents.items():
        if document is not None:
            validate_contract(audience, document, openapi, errors)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("API contract validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
