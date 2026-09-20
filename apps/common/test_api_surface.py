from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _sample_path(path: str, operation: dict[str, Any], path_item: dict[str, Any]) -> str:
    parameters = [*path_item.get("parameters", []), *operation.get("parameters", [])]
    schemas = {
        item["name"]: item.get("schema", {})
        for item in parameters
        if item.get("in") == "path" and isinstance(item.get("name"), str)
    }

    def replacement(match: re.Match[str]) -> str:
        name = match.group(1)
        schema = schemas.get(name, {})
        if schema.get("type") == "integer":
            return "1"
        if schema.get("format") == "uuid" or "public_id" in name.casefold():
            return "00000000-0000-0000-0000-000000000001"
        return "test"

    return re.sub(r"\{([^}]+)\}", replacement, path)


class OpenAPISurfaceTests(TestCase):
    """Exercise every documented operation at the HTTP boundary.

    Feature tests cover successful authenticated workflows.  This sweep adds a
    cheap invariant for the complete contract: every route must resolve, must
    not redirect, and must fail safely (never 5xx) when called without
    credentials and with a minimal JSON body.
    """

    maxDiff = None

    def setUp(self) -> None:
        cache.clear()

    def tearDown(self) -> None:
        # The sweep intentionally touches auth endpoints with invalid input;
        # do not leak their throttle counters into later workflow tests.
        cache.clear()

    def test_every_documented_operation_fails_safely_without_authentication(self) -> None:
        contract_path = Path(settings.BASE_DIR) / "contracts" / "openapi.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        client = APIClient()
        exercised = 0

        for documented_path, path_item in contract["paths"].items():
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                exercised += 1
                request_path = _sample_path(documented_path, operation, path_item)
                with self.subTest(method=method.upper(), path=documented_path):
                    response = client.generic(
                        method.upper(),
                        request_path,
                        data="{}",
                        content_type="application/json",
                        secure=True,
                        HTTP_X_REQUEST_ID="api-surface-contract-test",
                    )
                    self.assertNotIn(response.status_code, REDIRECT_STATUSES)
                    self.assertLess(response.status_code, 500)

        # 215 before organizations; the 28 added since are the scoped admin
        # resources, the learner join flow and scoped role revocation.
        # Pinned so a new endpoint cannot quietly skip this sweep.
        self.assertEqual(exercised, 243)
