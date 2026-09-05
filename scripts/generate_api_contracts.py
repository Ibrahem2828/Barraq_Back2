#!/usr/bin/env python3
"""Generate Baraq's client API-contract projections from Django and DRF.

The Django URLConf, view/serializer code, and the DRF Spectacular schema are
canonical.  This script only projects that source into the mobile/dashboard
artifacts; it is intentionally deterministic so CI can detect drift.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = ROOT / "contracts"
CANONICAL_PREFIX = "/api/v1/"
INTERNAL_PREFIX = "/api/internal/v1/"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def django_schema() -> dict[str, Any]:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from drf_spectacular.generators import SchemaGenerator

    return SchemaGenerator().get_schema(request=None, public=True)


def git_value(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def canonical_openapi(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove deprecated aliases and environment-specific server URLs."""

    document = copy.deepcopy(schema)
    document["paths"] = {
        path: item
        for path, item in document.get("paths", {}).items()
        if path.startswith((CANONICAL_PREFIX, INTERNAL_PREFIX))
    }
    document.pop("servers", None)
    document["x-baraq-api-base-url-env"] = "PUBLIC_API_BASE_URL"
    document["x-baraq-contract-source"] = "Django URLConf + DRF Spectacular"
    document["x-baraq-contract-scope"] = "canonical-v1-and-internal"
    return document


def add_code_truth_overrides(schema: dict[str, Any]) -> None:
    """Apply small, source-reviewed corrections Spectacular cannot infer.

    These are not extra APIs.  They make the schema match explicit view code
    where dynamic serializer selection or custom validation hides behavior
    from schema generation.  Each correction is emitted as a finding too.
    """

    components = schema.setdefault("components", {}).setdefault("schemas", {})
    feedback = components.get("AIFeedback")
    if feedback:
        feedback["properties"]["rating"]["minimum"] = 1
        feedback["properties"]["rating"]["maximum"] = 5
        # ModelSerializer marks fields whose model value is always present as
        # response-required.  Request requirements are represented separately.
        components["AIFeedbackRequest"] = {
            "type": "object",
            "properties": {
                key: copy.deepcopy(value)
                for key, value in feedback["properties"].items()
                if key
                not in {
                    "id",
                    "job",
                    "forwarded_to_ai_service",
                    "created_at",
                    "updated_at",
                }
            },
            "required": ["rating"],
            "description": "Request body accepted by AIFeedbackSerializer.",
        }
        for field in components["AIFeedbackRequest"]["properties"].values():
            field.pop("readOnly", None)

    character_response = components.get("SourceCharacterResponse")
    if character_response:
        character_response.setdefault("properties", {})["ai_job"] = {
            "$ref": "#/components/schemas/AIJob"
        }

    components["SourceProcessingQueuedResponse"] = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "source": {"$ref": "#/components/schemas/StudentSourceDetail"},
        },
        "required": ["message", "source"],
        "description": "Actual response returned by StudentSourceViewSet.process.",
    }

    paths = schema.get("paths", {})
    feedback_operation = paths.get("/api/v1/ai/jobs/{public_id}/feedback/", {}).get("post")
    if feedback_operation:
        feedback_operation["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/AIFeedbackRequest"}
                }
            },
        }
        feedback_operation["responses"] = {
            "200": {
                "description": "Existing feedback updated.",
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/AIFeedback"}}
                },
            },
            "201": {
                "description": "Feedback created.",
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/AIFeedback"}}
                },
            },
            "409": {
                "description": "Feedback is only accepted for completed AI jobs.",
                "content": {"application/json": {"schema": {"type": "object"}}},
            },
        }

    for prefix in ("/api/v1/student-sources/{id}/", "/api/v1/student-source-collections/{id}/"):
        process_operation = paths.get(prefix + "process/", {}).get("post")
        if process_operation:
            process_operation["responses"] = {
                "202": {
                    "description": "Source processing was queued or was already processing.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/SourceProcessingQueuedResponse"}
                        }
                    },
                }
            }
        for suffix in (
            "use-with-fahes/",
            "use-with-khota/",
            "use-with-rasheed/",
            "use-with-kholasa/",
            "use-with-sada/",
        ):
            operation = paths.get(prefix + suffix, {}).get("post")
            if operation:
                operation.pop("requestBody", None)
                operation["responses"] = {
                    "200": {
                        "description": "AI job creation result.",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SourceCharacterResponse"}
                            }
                        },
                    }
                }


def ref_name(schema: Any) -> str | None:
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref", "")
    prefix = "#/components/schemas/"
    return ref[len(prefix) :] if ref.startswith(prefix) else None


def rewrite_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: rewrite_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_refs(item) for item in value]
    if isinstance(value, str) and value.startswith("#/components/schemas/"):
        return value.replace("#/components/schemas/", "#/resources/", 1) + "/schema"
    return value


def field_metadata(name: str, definition: dict[str, Any], required: set[str], components: dict[str, Any]) -> dict[str, Any]:
    definition = copy.deepcopy(definition)
    target = definition
    name_from_ref = ref_name(definition)
    if name_from_ref and name_from_ref in components:
        target = {**components[name_from_ref], **definition}
    metadata: dict[str, Any] = {
        "name": name,
        "type": target.get("type"),
        "format": target.get("format"),
        "required": name in required,
        "nullable": target.get("nullable", False),
        "read_only": target.get("readOnly", False),
        "write_only": target.get("writeOnly", False),
        "enum": target.get("enum"),
        "minimum": target.get("minimum"),
        "maximum": target.get("maximum"),
        "min_length": target.get("minLength"),
        "max_length": target.get("maxLength"),
        "default": target.get("default"),
        "description": target.get("description"),
    }
    if name_from_ref:
        metadata["schema_ref"] = f"#/resources/{name_from_ref}/schema"
    return metadata


def resources_from_components(components: dict[str, Any]) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    for name, definition in sorted(components.items()):
        if not isinstance(definition, dict):
            continue
        required = set(definition.get("required", []))
        properties = definition.get("properties", {})
        resources[name] = {
            "schema": rewrite_refs(definition),
            "fields": [
                field_metadata(field, value, required, components)
                for field, value in properties.items()
            ],
        }
    return resources


def referenced_component_names(value: Any) -> set[str]:
    """Find all local OpenAPI schema references in a JSON-like value."""

    found: set[str] = set()
    if isinstance(value, dict):
        name = ref_name(value)
        if name:
            found.add(name)
        for item in value.values():
            found.update(referenced_component_names(item))
    elif isinstance(value, list):
        for item in value:
            found.update(referenced_component_names(item))
    return found


def components_for_paths(openapi: dict[str, Any], paths: dict[str, Any]) -> dict[str, Any]:
    """Keep each client artifact free of schemas reachable only by another audience."""

    all_components = openapi.get("components", {}).get("schemas", {})
    pending = referenced_component_names(paths)
    selected: set[str] = set()
    while pending:
        name = pending.pop()
        if name in selected or name not in all_components:
            continue
        selected.add(name)
        pending.update(referenced_component_names(all_components[name]))
    return {name: copy.deepcopy(all_components[name]) for name in sorted(selected)}


def enum_catalog(components: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, definition in sorted(components.items()):
        if isinstance(definition, dict) and "enum" in definition:
            result[name] = {
                "type": definition.get("type"),
                "values": definition["enum"],
                "nullable": definition.get("nullable", False),
            }
    result["AIFeedbackRating"] = {
        "type": "integer",
        "minimum": 1,
        "maximum": 5,
        "source": "apps.ai_integration.serializers.AIFeedbackSerializer.validate_rating",
    }
    return result


def sample_path(path: str) -> str:
    values = {
        "public_id": "00000000-0000-0000-0000-000000000000",
        "id": "1",
        "pk": "1",
    }
    return re.sub(r"\{([^}]+)\}", lambda match: values.get(match.group(1), "1"), path)


def view_metadata(path: str, method: str) -> dict[str, Any]:
    """Resolve a contract operation back to the live Django callback."""

    from django.urls import resolve

    matched = resolve(sample_path(path))
    callback = matched.func
    view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if view_class is None:
        return {"url_file": "config/urls.py", "view": None, "serializer": None, "permission": []}

    action = getattr(callback, "actions", {}).get(method.lower())
    instance = view_class()
    if action:
        instance.action = action

    serializer = None
    try:
        serializer_class = instance.get_serializer_class()
        serializer = f"{serializer_class.__module__}.{serializer_class.__name__}"
    except (AttributeError, AssertionError):
        serializer = None

    permission_classes = getattr(instance, "permission_classes", [])
    permissions = [f"{item.__module__}.{item.__name__}" for item in permission_classes]
    module = view_class.__module__
    module_parts = module.split(".")
    if module_parts[:1] == ["apps"] and len(module_parts) >= 2:
        app_urls = f"apps/{module_parts[1]}/urls.py"
    else:
        app_urls = "config/urls.py"
    return {
        "url_file": f"config/urls.py; config/api_urls.py; {app_urls}",
        "view": f"{module}.{view_class.__name__}",
        "serializer": serializer,
        "permission": permissions,
        "action": action,
    }


def authentication(metadata: dict[str, Any]) -> dict[str, Any]:
    permission_names = metadata["permission"]
    allow_any = any(name.endswith(".AllowAny") for name in permission_names)
    is_admin_dashboard = any(name.endswith(".IsAdminDashboardUser") for name in permission_names)
    is_staff = any(name.endswith(".IsAdminUser") for name in permission_names)
    roles: list[str] = []
    if is_admin_dashboard or is_staff:
        roles = ["admin", "support", "super_admin"]
    permissions = [name.rsplit(".", 1)[-1] for name in permission_names if not name.endswith(".IsAuthenticated")]
    return {
        "required": not allow_any,
        "type": "Bearer JWT" if not allow_any else "none",
        "roles": roles,
        "permissions": permissions,
        "mechanism": "Authorization: Bearer <access_token>" if not allow_any else None,
    }


def required_admin_permission(metadata: dict[str, Any]) -> list[str]:
    """Obtain an action-specific RBAC code without fabricating an RBAC rule."""

    try:
        from django.utils.module_loading import import_string

        view_path = metadata.get("view")
        if not view_path:
            return []
        view_class = import_string(view_path)
        instance = view_class()
        if metadata.get("action"):
            instance.action = metadata["action"]
        value = instance.get_required_permission() if hasattr(instance, "get_required_permission") else getattr(instance, "required_permission", None)
        return [value] if value else []
    except (AttributeError, ImportError):
        return []


def ownership_for(path: str, audience: str) -> dict[str, Any]:
    if audience == "dashboard":
        return {"required": False, "rule": "No per-resource ownership check; access is governed by dashboard RBAC."}
    if path.startswith(("/api/v1/auth/", "/api/v1/education-stages/", "/api/v1/subjects/", "/api/v1/subscriptions/plans/")):
        return {"required": False, "rule": "Public endpoint; no ownership constraint in the view."}
    if path.startswith("/api/v1/question-bank/"):
        return {"required": True, "rule": "Object visibility is public or created_by the authenticated user."}
    if path.startswith(("/api/v1/users/me/", "/api/v1/students/", "/api/v1/subscriptions/me/")):
        return {"required": True, "rule": "The endpoint is bound to request.user."}
    if any(
        path.startswith(prefix)
        for prefix in (
            "/api/v1/projects/",
            "/api/v1/student-sources/",
            "/api/v1/student-source-collections/",
            "/api/v1/ai/jobs/",
            "/api/v1/quizzes/",
            "/api/v1/quiz-questions/",
            "/api/v1/quiz-attempts/",
            "/api/v1/study-plans/",
            "/api/v1/study-tasks/",
            "/api/v1/summaries/",
            "/api/v1/transcriptions/",
            "/api/v1/recommendations/",
            "/api/v1/notifications/",
            "/api/v1/support/tickets/",
            "/api/v1/users/subjects/",
        )
    ):
        return {"required": True, "rule": "Queryset/object access is scoped to request.user in the view or selector."}
    return {"required": False, "rule": "No object ownership rule is enforced by this endpoint."}


def throttle_for(path: str, method: str) -> dict[str, Any]:
    scope = None
    if path == "/api/v1/auth/register/" and method == "POST":
        scope = "register"
    elif path == "/api/v1/auth/login/" and method == "POST":
        scope = "login"
    elif path in {"/api/v1/auth/password-reset/", "/api/v1/auth/password-reset/confirm/"} and method == "POST":
        scope = "password_reset"
    elif path.startswith("/api/v1/ai/jobs/") and path == "/api/v1/ai/jobs/" and method == "POST":
        scope = "ai_requests"
    elif path.startswith("/api/v1/student-sources/") and method == "POST":
        if path.endswith("/process/") or path == "/api/v1/student-sources/":
            scope = "uploads"
        elif "/use-with-" in path:
            scope = "ai_requests"
    return {
        "scope": scope,
        "known": True,
        "global_scopes": ["anon", "user", "scoped"],
        "configured_default_rates": {
            "anon": "60/hour",
            "user": "2000/day",
            "register": "10/hour",
            "login": "10/minute",
            "password_reset": "5/hour",
            "uploads": "30/hour",
            "ai_requests": "100/day",
        },
        "note": "Rates are environment-configurable Django settings; the values above are source defaults.",
    }


def endpoint_parameters(operation: dict[str, Any], location: str) -> list[dict[str, Any]]:
    result = []
    for parameter in operation.get("parameters", []):
        if parameter.get("in") == location:
            result.append(
                {
                    "name": parameter.get("name"),
                    "required": parameter.get("required", False),
                    "schema": rewrite_refs(parameter.get("schema", {})),
                    "description": parameter.get("description"),
                }
            )
    return result


def content_schemas(content: dict[str, Any]) -> tuple[list[str], Any]:
    content_types = sorted(content)
    if not content_types:
        return [], None
    first = content[content_types[0]]
    return content_types, rewrite_refs(first.get("schema"))


def response_definitions(operation: dict[str, Any]) -> dict[str, Any]:
    responses: dict[str, Any] = {}
    for status_code, payload in operation.get("responses", {}).items():
        content_types, schema = content_schemas(payload.get("content", {}))
        responses[str(status_code)] = {
            "description": payload.get("description", ""),
            "content_types": content_types,
            "schema": schema,
        }
    return responses


def endpoint_envelope(statuses: dict[str, Any]) -> dict[str, Any] | None:
    if set(statuses) == {"204"}:
        return None
    return {
        "applied": True,
        "data_schema_location": "data for successful JSON responses; explicit payloads with success are preserved",
        "request_id_field": "request_id when a request exists",
        "note": "The response schemas in this contract describe the inner data value before EnvelopeJSONRenderer wraps it.",
    }


def is_paginated(operation: dict[str, Any], responses: dict[str, Any]) -> dict[str, Any] | None:
    if any(param.get("name") == "page" and param.get("in") == "query" for param in operation.get("parameters", [])):
        return {
            "type": "page_number",
            "page_query_parameter": "page",
            "page_size_query_parameter": "page_size",
            "default_page_size": 20,
            "maximum_page_size": 100,
            "wrapped_data_location": "data",
            "meta": ["count", "next", "previous"],
        }
    if any("Paginated" in str(item.get("schema")) for item in responses.values()):
        return {
            "type": "page_number",
            "page_query_parameter": "page",
            "page_size_query_parameter": "page_size",
            "default_page_size": 20,
            "maximum_page_size": 100,
            "wrapped_data_location": "data",
            "meta": ["count", "next", "previous"],
        }
    return None


def headers_for(auth: dict[str, Any], content_types: list[str]) -> list[dict[str, Any]]:
    headers = [
        {
            "name": "X-Request-ID",
            "required": False,
            "schema": {"type": "string", "pattern": "^[A-Za-z0-9._-]{8,128}$"},
            "description": "Optional correlation ID; an invalid or omitted value is replaced by the server.",
        }
    ]
    if auth["required"]:
        headers.insert(
            0,
            {
                "name": "Authorization",
                "required": True,
                "schema": {"type": "string", "format": "Bearer JWT"},
                "description": "Access token from the shared JWT authentication API.",
            },
        )
    if content_types:
        headers.append(
            {
                "name": "Content-Type",
                "required": True,
                "schema": {"type": "string", "enum": content_types},
                "description": "Use one of the parser-supported request representations.",
            }
        )
    return headers


def operation_endpoint(path: str, method: str, operation: dict[str, Any], audience: str) -> dict[str, Any]:
    source = view_metadata(path, method)
    auth = authentication(source)
    required_permission = required_admin_permission(source) if audience == "dashboard" else []
    if required_permission:
        auth["permissions"].extend(required_permission)
    request_body = operation.get("requestBody", {})
    content_types, request_schema = content_schemas(request_body.get("content", {}))
    responses = response_definitions(operation)
    tag = (operation.get("tags") or ["Uncategorized"])[0]
    return {
        "id": operation.get("operationId") or f"{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}",
        "domain": re.sub(r"[^a-z0-9]+", "_", tag.lower()).strip("_"),
        "name": operation.get("summary") or operation.get("operationId") or method.upper(),
        "method": method.upper(),
        "path": path,
        "audience": audience,
        "description": operation.get("description", ""),
        "authentication": auth,
        "ownership": ownership_for(path, audience),
        "throttling": throttle_for(path, method.upper()),
        "path_parameters": endpoint_parameters(operation, "path"),
        "query_parameters": endpoint_parameters(operation, "query"),
        "headers": headers_for(auth, content_types),
        "content_type": content_types[0] if len(content_types) == 1 else (content_types or None),
        "content_types": content_types,
        "request": {
            "required": request_body.get("required", False),
            "schema": request_schema,
            "example": None,
        },
        "responses": responses,
        "response_envelope": endpoint_envelope(responses),
        "pagination": is_paginated(operation, responses),
        "idempotency": {
            "supported": path == "/api/v1/ai/jobs/" and method.upper() == "POST",
            "key": "server-derived from user, task/context, normalized input, and parameters" if path == "/api/v1/ai/jobs/" and method.upper() == "POST" else None,
        },
        "side_effects": [],
        "related_endpoints": [],
        "backend_source": source,
        "notes": [],
    }


def mobile_paths(openapi: dict[str, Any]) -> dict[str, Any]:
    excluded_system = {
        "/api/v1/",
        "/api/v1/health/",
        "/api/v1/health/live/",
        "/api/v1/health/ready/",
        "/api/v1/meta/",
        "/api/v1/auth/",
        "/api/v1/ai/",
        "/api/v1/subscriptions/",
    }
    excluded_privileged = {"/api/v1/ai/service-health/"}
    return {
        path: item
        for path, item in openapi["paths"].items()
        if path.startswith(CANONICAL_PREFIX)
        and not path.startswith("/api/v1/admin/")
        and path not in excluded_system
        and path not in excluded_privileged
    }


def dashboard_paths(openapi: dict[str, Any]) -> dict[str, Any]:
    shared_auth = {
        "/api/v1/auth/login/",
        "/api/v1/auth/refresh/",
        "/api/v1/auth/verify/",
        "/api/v1/auth/logout/",
        "/api/v1/auth/change-password/",
        "/api/v1/auth/password-reset/",
        "/api/v1/auth/password-reset/confirm/",
    }
    return {
        path: item
        for path, item in openapi["paths"].items()
        if path.startswith("/api/v1/admin/")
        or path in shared_auth
        or path == "/api/v1/ai/service-health/"
    }


def envelope() -> dict[str, Any]:
    return {
        "renderer": "apps.common.renderers.EnvelopeJSONRenderer",
        "success": {
            "shape": {"success": True, "message": "string", "data": "any", "request_id": "string|null"},
            "rule": "Raw successful DRF data is wrapped under data with message 'Success', unless the view already returns a mapping containing success.",
        },
        "paginated_success": {
            "shape": {"success": True, "message": "Success", "data": "array", "meta": {"count": "integer", "next": "string|null", "previous": "string|null"}, "request_id": "string|null"},
            "rule": "DRF {count, next, previous, results} is transformed into data and meta.",
        },
        "validation_error": {
            "shape": {"success": False, "message": "Validation error", "code": "validation_error", "errors": "field errors", "request_id": "string|null"},
            "status": 400,
        },
        "authentication_error": {
            "shape": {"success": False, "message": "string", "code": "authentication_error", "errors": {"detail": "string"}, "detail": "string", "request_id": "string|null"},
            "status": 401,
        },
        "permission_error": {
            "shape": {"success": False, "message": "string", "code": "permission_denied", "errors": {"detail": "string"}, "detail": "string", "request_id": "string|null"},
            "status": 403,
        },
        "not_found_error": {
            "shape": {"success": False, "message": "string", "code": "not_found", "errors": {"detail": "string"}, "detail": "string", "request_id": "string|null"},
            "status": 404,
        },
        "rate_limit_error": {
            "shape": {"success": False, "message": "string", "code": "request_error", "errors": {"detail": "string"}, "detail": "string", "request_id": "string|null"},
            "status": 429,
            "note": "429 has no dedicated code in DEFAULT_ERROR_CODES, so the current handler emits request_error.",
        },
        "server_error": {
            "shape": {"success": False, "message": "Internal server error", "code": "server_error", "errors": "{} outside DEBUG", "request_id": "string|null"},
            "status": 500,
        },
        "no_content": {"status": 204, "body": None},
    }


def pagination() -> dict[str, Any]:
    return {
        "class": "apps.common.pagination.StandardResultsSetPagination",
        "type": "page_number",
        "page_query_parameter": "page",
        "page_size_query_parameter": "page_size",
        "default_page_size": 20,
        "maximum_page_size": 100,
        "renderer_result_location": "data",
        "renderer_meta_location": "meta",
        "meta_fields": ["count", "next", "previous"],
        "exceptions": "Views that set pagination_class = None return an unpaginated data array.",
    }


def common_errors() -> dict[str, Any]:
    return {
        "handler": "apps.common.exceptions.custom_exception_handler",
        "codes": {
            "400": "validation_error",
            "401": "authentication_error",
            "403": "permission_denied",
            "404": "not_found",
            "500": "server_error",
            "other_handled_statuses": "request_error",
        },
        "note": "Endpoint responses list only source-declared success/status branches. Global DRF exceptions can still produce the documented 400/401/403/404/429/500 envelopes where applicable.",
    }


def mobile_flows() -> dict[str, Any]:
    return {
        "ai_jobs": {
            "client_boundary": "Mobile calls Django only. Django dispatches to the internal Baraq AI service; internal /api/internal/v1/ai/ routes are excluded.",
            "creation_endpoint": {"method": "POST", "path": "/api/v1/ai/jobs/"},
            "polling_endpoint": {"method": "GET", "path": "/api/v1/ai/jobs/{public_id}/"},
            "refresh_endpoint": {"method": "POST", "path": "/api/v1/ai/jobs/{public_id}/refresh/"},
            "cancellation_endpoint": {"method": "POST", "path": "/api/v1/ai/jobs/{public_id}/cancel/"},
            "task_types": {
                "fahes_generate_quiz": {"character": "fahes", "requires": "source or collection"},
                "khota_generate_plan": {"character": "khota", "requires": "subject or input.subject_ids after normalization"},
                "rasheed_recommendations": {"character": "rasheed", "requires": "no source or collection"},
                "kholasa_generate_summary": {"character": "kholasa", "requires": "source or collection"},
                "sada_transcribe_audio": {"character": "sada", "requires": "exactly one owned audio source; collections are rejected"},
            },
            "normalized_task_input": {
                "fahes_generate_quiz": {
                    "source_ids": "Derived by Django from one source or up to 10 usable collection sources; never client-supplied.",
                    "question_count": {"type": "integer", "minimum": 3, "maximum": 50, "default": 10, "accepts_from": ["input.question_count", "parameters.question_count", "parameters.questions_count"]},
                    "question_types": {"type": "array[string]", "values": ["mcq", "true_false"], "maximum_items": 2, "default": ["mcq", "true_false"]},
                    "language": {"values": ["ar", "en"], "default": "ar"},
                    "optional": {"subject_id": "derived from subject", "topic": "text <=300", "instructions": "text <=1000", "difficulty": ["easy", "medium", "hard"]},
                },
                "kholasa_generate_summary": {
                    "source_ids": "Derived by Django from one source or up to 10 usable collection sources; never client-supplied.",
                    "summary_length": {"values": ["short", "medium", "detailed"], "default": "medium"},
                    "focus_topics": {"type": "array[string]", "maximum_items": 30},
                    "include_review_questions": {"type": "boolean", "default": True},
                    "include_flashcards": {"type": "boolean", "default": True},
                    "language": {"values": ["ar", "en"], "default": "ar"},
                    "optional": {"instructions": "text <=1000"},
                },
                "khota_generate_plan": {
                    "source_ids": "Derived by Django from one source or up to 10 usable collection sources; may be empty.",
                    "subject_ids": "Derived from request subject when present, otherwise input.subject_ids (at most 20 text values); at least one is required.",
                    "start_date": {"format": "date", "default": "server local date"},
                    "end_date": {"format": "date", "default": "start_date + 6 days", "constraint": "0..180 days from start_date"},
                    "daily_available_minutes": {"type": "integer", "minimum": 20, "maximum": 720, "default": 60},
                    "exam_dates": "object with string subject ids and ISO-date values",
                    "weak_topics": {"type": "array[string]", "maximum_items": 50},
                    "excluded_dates": {"type": "array[date]", "maximum_items": 60},
                    "preferred_session_minutes": {"type": "integer", "minimum": 15, "maximum": 180, "default": 45},
                    "language": {"values": ["ar", "en"], "default": "ar"},
                },
                "rasheed_recommendations": {
                    "metrics": "Django derives submitted-quiz attempts and average percentage; clients cannot supply authoritative metrics.",
                    "topic_performance": [],
                    "recent_actions": [],
                    "language": {"values": ["ar", "en"], "default": "ar"},
                    "optional": {"learner_goal": "text <=500"},
                },
                "sada_transcribe_audio": {
                    "source_id": "Derived by Django from the owned audio source; exactly one source is required.",
                    "language": {"type": "alphabetic string", "minimum_length": 2, "maximum_length": 5, "default": "ar"},
                    "diarize": {"type": "boolean", "default": False},
                    "known_terms": {"type": "array[string]", "maximum_items": 200},
                    "cleanup_level": {"values": ["literal", "light", "educational"], "default": "educational"},
                },
                "model_policy": {"tier": ["fast", "balanced", "high_quality"], "allow_fallback": {"type": "boolean", "default": True}},
            },
            "legacy_task_type_aliases_accepted_on_create": {
                "kholasa_summary": "kholasa_generate_summary",
                "sada_transcription": "sada_transcribe_audio",
                "kholasa_summarize": "kholasa_generate_summary",
                "sada_transcribe": "sada_transcribe_audio",
                "rasheed_recommend": "rasheed_recommendations",
            },
            "job_statuses": ["created", "queued", "submitted", "processing", "validating", "output_ready", "materializing", "completed", "failed", "canceled"],
            "result_delivery": "Completed jobs have result_type and result_id. Materialized results are read through summaries, quizzes, study plans, recommendations, or transcriptions as applicable.",
            "failure": "Failed jobs expose error_code and an end-user-safe error_message; reserved usage is refunded when credits were not committed.",
            "idempotency": "Django derives an idempotency key from the user, task/context, normalized input and parameters. Matching non-terminal/nonfailed jobs are returned with HTTP 200; force=true creates a fresh key.",
            "usage": "Django reserves character usage when a job is created, commits on completion, and refunds eligible failed/canceled jobs.",
            "source_context": "Request fields are singular project, source, collection, subject plus input and parameters. There is no source_ids, collection_id, or user_instruction top-level request field.",
            "source_shortcuts": "Owned source/collection character actions create the same server-side AI job flow; generic use-with-character accepts character and optional action, while fixed-character shortcuts accept no request schema.",
            "feedback": {
                "endpoint": "POST /api/v1/ai/jobs/{public_id}/feedback/",
                "eligible_status": "completed only",
                "ownership": "The job queryset is scoped to request.user.",
                "rating": "integer 1..5",
                "feedback_type_values": ["general", "incorrect", "not_grounded", "unclear", "too_easy", "too_hard", "too_long", "too_short", "other"],
                "upsert": "Unique (job, user); a repeat POST updates the same feedback and returns 200, first POST returns 201.",
                "forwarding": "Django schedules forwarding to the internal AI service after commit; Mobile never calls that service.",
            },
        },
        "project_source_relationship": {
            "implemented": "Project is an owned UUID-keyed workspace. Sources, source collections, AI jobs, quizzes and study plans each have optional project foreign keys.",
            "constraints": "A source/collection project must be owned by the user; a source or collection with a project makes an AI job inherit that project and rejects a mismatching request project. A source in a collection inherits the collection project.",
            "difference_from_preferred_architecture": "Summaries, transcriptions and recommendations relate to AI jobs and source/collection (where applicable), not directly to Project.",
        },
        "source_upload": {
            "creation_endpoint": "POST /api/v1/student-sources/",
            "request_requirements": "title and file are required; the view requires request.FILES so a file upload representation is required in practice.",
            "maximum_file_size": "STUDENT_SOURCE_MAX_UPLOAD_MB (source default: 25 MB; environment-configurable)",
            "allowed_extensions": ["pdf", "txt", "jpg", "jpeg", "png", "webp", "doc", "docx", "ppt", "pptx", "mp3", "m4a", "wav"],
            "derived_source_types": {"pdf": "pdf", "txt": "text", "images": "image", "office_documents": "document|presentation", "audio": "audio"},
            "validation": "The backend checks filename extension, file signature and Office archive safety. It rejects executable/archive/script-style dangerous extensions.",
            "states": ["uploaded", "processing", "ready", "failed"],
            "processing": "Upload queues processing. Text files are extracted locally and become ready; other accepted files are stored with processing metadata awaiting AI-service extraction. POST process queues processing and returns 202 with message and source.",
            "ownership_and_context": "Optional project, subject and collection must be owned/active as validated. Collection subject/project can be inherited; mismatching project values are rejected.",
        },
        "subscriptions_and_usage": {
            "plan_catalog": "GET /api/v1/subscriptions/plans/ is public and lists active public plans.",
            "current_entitlement": "GET /api/v1/subscriptions/me/ lazily creates/returns the current subscription plus plan, usage, limits, features and remaining entitlement.",
            "client_mutations": "No mobile subscription upgrade, cancellation or payment endpoint is implemented.",
        },
    }


def dashboard_flows() -> dict[str, Any]:
    return {
        "authentication": "There is no separate dashboard login/session endpoint. The dashboard uses the shared JWT endpoints, then GET /api/v1/admin/me/ to obtain active roles, permission codes and allowed sections.",
        "rbac": "Most /api/v1/admin/ operations require IsAdminDashboardUser plus action-specific HasAdminPermission. /api/v1/ai/service-health/ instead uses Django IsAdminUser.",
        "ai_operations": "Dashboard AI jobs are read-only except cancel. Metrics aggregates job statuses/characters and feedback count/average rating. Result resources are read-only.",
        "support": "Support ticket updates and messages require support.manage; reads require support.view. Non-internal admin replies set ticket status to waiting_user.",
    }


def findings(audience: str) -> list[dict[str, Any]]:
    common = [
        {
            "severity": "warning",
            "code": "openapi_feedback_operation_mismatch",
            "evidence": "DRF Spectacular selects AIJobSerializer for the feedback action, but AIJobViewSet.feedback explicitly validates and returns AIFeedbackSerializer with 201/200 and 409 branches.",
            "contract_treatment": "The generated projection overrides only this operation from the concrete view code.",
        },
        {
            "severity": "warning",
            "code": "openapi_source_character_response_omits_ai_job",
            "evidence": "SourceCharacterResponseSerializer does not declare ai_job, while StudentSourceViewSet/CollectionViewSet _build_character_response adds AIJobSerializer(ai_job).data.",
            "contract_treatment": "SourceCharacterResponse includes optional ai_job from concrete response-building code.",
        },
        {
            "severity": "warning",
            "code": "openapi_source_process_response_mismatch",
            "evidence": "The process action returns {message, source} with 202, while its decorator declares StudentSourceDetailSerializer.",
            "contract_treatment": "The projection uses SourceProcessingQueuedResponse for the source process route.",
        },
        {
            "severity": "info",
            "code": "spectacular_schema_inner_data_only",
            "evidence": "The current DRF schema describes serializer data, whereas EnvelopeJSONRenderer wraps rendered JSON responses.",
            "contract_treatment": "Every contract endpoint explicitly points to the exact renderer envelope.",
        },
        {
            "severity": "info",
            "code": "legacy_api_aliases_excluded",
            "evidence": "config.urls includes /api/ compatibility routes with deprecation headers and sunset Wed, 31 Dec 2026 23:59:59 GMT.",
            "contract_treatment": "Only canonical /api/v1/ paths are included.",
        },
    ]
    if audience == "mobile":
        common.extend(
            [
                {
                    "severity": "info",
                    "code": "privileged_ai_service_health_excluded",
                    "evidence": "/api/v1/ai/service-health/ is protected by IsAdminUser despite being outside /api/v1/admin/.",
                    "contract_treatment": "Excluded from Mobile.",
                },
                {
                    "severity": "info",
                    "code": "internal_ai_routes_excluded",
                    "evidence": "config.urls mounts internal AI webhooks and manifests under /api/internal/v1/ai/ with service-key or webhook verification.",
                    "contract_treatment": "Excluded from Mobile.",
                },
            ]
        )
    return common


def missing_capabilities(audience: str) -> list[dict[str, Any]]:
    if audience == "mobile":
        return [
            {
                "proposed": True,
                "desired_capability": "Subscription self-service purchase, upgrade, downgrade, and cancellation",
                "why": "Mobile can read plans and its current entitlement, but has no backend payment or plan-change route.",
                "recommended_endpoint": "POST /api/v1/subscriptions/checkout/ (proposed)",
                "recommended_request_schema": {"plan_code": "string", "platform": "string"},
                "recommended_response_schema": {"checkout_url_or_platform_payload": "string|object", "subscription": "UserSubscription"},
                "backend_changes_required": "Implement provider-safe payment orchestration and entitlement transition validation; do not trust client payment state.",
            },
            {
                "proposed": True,
                "desired_capability": "Gamification profile, points, streaks, achievements and leaderboard",
                "why": "No gamification models or client routes exist in the current backend.",
                "recommended_endpoint": "GET /api/v1/gamification/me/ (proposed)",
                "recommended_request_schema": None,
                "recommended_response_schema": {"points": "integer", "streak": "integer", "achievements": "array"},
                "backend_changes_required": "Add authoritative models, event ledger, privacy rules and routes.",
            },
            {
                "proposed": True,
                "desired_capability": "Notification delivery preferences",
                "why": "Notification read/list actions exist, but no preference model or endpoint exists.",
                "recommended_endpoint": "GET/PATCH /api/v1/notifications/preferences/ (proposed)",
                "recommended_request_schema": {"category_preferences": "object"},
                "recommended_response_schema": {"category_preferences": "object"},
                "backend_changes_required": "Add an owned preference model and enforce it in notification producers.",
            },
            {
                "proposed": True,
                "desired_capability": "Text-body and URL source creation",
                "why": "StudentSource has text/link source types, but the implemented create serializer requires a file upload; no text-body or URL ingestion route exists.",
                "recommended_endpoint": "POST /api/v1/student-sources/text/ and POST /api/v1/student-sources/link/ (proposed)",
                "recommended_request_schema": {"title": "string", "content_or_url": "string", "project": "integer|null"},
                "recommended_response_schema": "StudentSourceDetail",
                "backend_changes_required": "Add source storage/validation/processing paths without bypassing ownership and quota checks.",
            },
            {
                "proposed": True,
                "desired_capability": "Structured student performance analytics",
                "why": "Mobile has AI-generated recommendations, but no direct performance/time-series analytics endpoint.",
                "recommended_endpoint": "GET /api/v1/analytics/me/performance/ (proposed)",
                "recommended_request_schema": None,
                "recommended_response_schema": {"period": "object", "quiz_metrics": "object", "study_plan_metrics": "object"},
                "backend_changes_required": "Define authoritative aggregation, pagination/time windows and privacy boundaries.",
            },
        ]
    return [
        {
            "proposed": True,
            "desired_capability": "Dashboard project operations and inspection",
            "why": "Projects are mobile owner-scoped only; no /api/v1/admin/projects/ route exists.",
            "recommended_endpoint": "GET /api/v1/admin/projects/ (proposed)",
            "recommended_request_schema": None,
            "recommended_response_schema": {"data": "paginated admin project projection"},
            "backend_changes_required": "Add RBAC-protected project serializer, filters and audit logging.",
        },
        {
            "proposed": True,
            "desired_capability": "Structured AI provider/model/latency/token/cost and prompt-version analytics",
            "why": "AIJob exposes service_metadata, quality_metrics and security_flags JSON but no stable structured fields, filters or cost aggregation API.",
            "recommended_endpoint": "GET /api/v1/admin/ai-jobs/analytics/ (proposed)",
            "recommended_request_schema": None,
            "recommended_response_schema": {"by_provider": "array", "by_model": "array", "latency": "object", "tokens": "object", "estimated_cost": "object"},
            "backend_changes_required": "Persist typed telemetry fields, define retention/access controls, filters and aggregation.",
        },
        {
            "proposed": True,
            "desired_capability": "Feedback filters and reports by character, provider, model, date, feedback type and status",
            "why": "Admin AI feedback currently filters only rating and training_consent; it has no report aggregate endpoint.",
            "recommended_endpoint": "GET /api/v1/admin/ai-feedback/metrics/ (proposed)",
            "recommended_request_schema": None,
            "recommended_response_schema": {"count": "integer", "average_rating": "number|null", "breakdowns": "object"},
            "backend_changes_required": "Expose only approved telemetry snapshots and add indexed server-side filters/aggregates.",
        },
        {
            "proposed": True,
            "desired_capability": "Security-flagged AI-job filtering and investigation workflow",
            "why": "AIJob serializes security_flags but AdminAIJobViewSet has no security flag filter or moderation action.",
            "recommended_endpoint": "GET /api/v1/admin/ai-jobs/?security_flag=... (proposed)",
            "recommended_request_schema": None,
            "recommended_response_schema": "Paginated AIJob list",
            "backend_changes_required": "Define flag taxonomy, indexes, RBAC permissions and audit trail for disposition actions.",
        },
        {
            "proposed": True,
            "desired_capability": "Admin notification management",
            "why": "Notifications are only exposed through an owner-scoped mobile viewset.",
            "recommended_endpoint": "GET/POST /api/v1/admin/notifications/ (proposed)",
            "recommended_request_schema": {"user": "integer", "category": "enum", "title": "string", "body": "string"},
            "recommended_response_schema": "Admin notification projection",
            "backend_changes_required": "Add RBAC rules, idempotency and audit logging; avoid arbitrary client-driven notification delivery.",
        },
    ]


def client_contract(openapi: dict[str, Any], audience: str) -> dict[str, Any]:
    selected_paths = mobile_paths(openapi) if audience == "mobile" else dashboard_paths(openapi)
    endpoints = []
    for path, path_item in sorted(selected_paths.items()):
        for method, operation in path_item.items():
            if method.lower() in HTTP_METHODS:
                endpoints.append(operation_endpoint(path, method, operation, audience))
    endpoints.sort(key=lambda item: (item["path"], item["method"], item["id"]))

    commit = git_value("rev-parse", "HEAD")
    components = components_for_paths(openapi, selected_paths)
    title = "Baraq Mobile API Contract" if audience == "mobile" else "Baraq Dashboard API Contract"
    contract = {
        "contract": {
            "name": title,
            "version": "1.0.0",
            "compatible_backend_contract_version": "2.0",
            "generated_from_backend_commit": commit,
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "audience": audience,
            "status": "candidate",
            "api_base_path": "/api/v1",
            "api_base_url_env": "PUBLIC_API_BASE_URL",
            "source_of_truth": "django_backend",
            "notes": [
                "Generated deterministically from the current Django URLConf, DRF Spectacular schema and source-reviewed dynamic view behavior.",
                "generated_at records artifact generation time; CI ignores generation metadata while checking semantic drift.",
                "Endpoint schemas describe the inner serializer payload; response_envelope describes the rendered HTTP body.",
            ],
        },
        "authentication": {
            "default": "Bearer JWT via rest_framework_simplejwt.authentication.JWTAuthentication",
            "authorization_header": "Authorization: Bearer <access_token>",
            "access_token_lifetime": "ACCESS_TOKEN_LIFETIME_MINUTES (source default: 30 minutes)",
            "refresh_token_lifetime": "REFRESH_TOKEN_LIFETIME_DAYS (source default: 14 days)",
            "refresh_rotation": True,
            "blacklist_after_rotation": True,
            "dashboard_rbac": "IsAdminDashboardUser plus HasAdminPermission for most admin routes." if audience == "dashboard" else None,
        },
        "response_envelope": envelope(),
        "pagination": pagination(),
        "errors": common_errors(),
        "enums": enum_catalog(components),
        "resources": resources_from_components(components),
        "endpoints": endpoints,
        "flows": mobile_flows() if audience == "mobile" else dashboard_flows(),
        "deprecated": [
            {
                "path_prefix": "/api/",
                "replacement": "/api/v1/",
                "status": "deprecated",
                "sunset": "Wed, 31 Dec 2026 23:59:59 GMT",
                "source": "config.urls and APIVersionHeadersMiddleware",
            }
        ],
        "missing_expected_capabilities": missing_capabilities(audience),
        "validation_findings": findings(audience),
    }
    return contract


def serialize(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_or_check(outputs: dict[Path, Any], check: bool) -> int:
    outdated = []
    for path, document in outputs.items():
        content = serialize(document)
        if check:
            if not path.exists():
                outdated.append(path)
                continue
            try:
                committed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                outdated.append(path)
                continue
            expected = copy.deepcopy(document)
            # Backend commits unrelated to an API surface must not force a
            # byte-for-byte artifact refresh. These fields remain useful audit
            # metadata when the artifact is deliberately regenerated.
            for candidate in (committed, expected):
                metadata = candidate.get("contract") if isinstance(candidate, dict) else None
                if isinstance(metadata, dict):
                    metadata.pop("generated_from_backend_commit", None)
                    metadata.pop("generated_at", None)
            if committed != expected:
                outdated.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"Generated {path.relative_to(ROOT)}")
    if outdated:
        for path in outdated:
            print(f"OUTDATED: {path.relative_to(ROOT)}")
        return 1
    if check:
        print("API contract artifacts are current.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when committed artifacts are not current.")
    args = parser.parse_args()

    schema = django_schema()
    add_code_truth_overrides(schema)
    openapi = canonical_openapi(schema)
    outputs = {
        CONTRACTS_DIR / "openapi.json": openapi,
        CONTRACTS_DIR / "mobile_api_contract.json": client_contract(openapi, "mobile"),
        CONTRACTS_DIR / "dashboard_api_contract.json": client_contract(openapi, "dashboard"),
    }
    return write_or_check(outputs, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
