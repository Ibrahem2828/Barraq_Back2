#!/usr/bin/env python3
"""Export deterministic, canonical API contracts from the live URL configuration."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "api"
CANONICAL_PUBLIC_PREFIX = "/api/v1/"
INTERNAL_PREFIX = "/api/internal/v1/"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _schema():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from drf_spectacular.generators import SchemaGenerator

    return SchemaGenerator().get_schema(request=None, public=True)


def _filtered_schema(schema, *, title, description, include_path):
    exported = copy.deepcopy(schema)
    exported["info"] = {**exported["info"], "title": title, "description": description}
    exported["paths"] = {
        path: operations
        for path, operations in exported.get("paths", {}).items()
        if include_path(path)
    }
    exported["x-baraq-contract-source"] = "Django URLConf + drf-spectacular"
    exported["x-baraq-contract-scope"] = "canonical-v1"
    return exported


def build_contracts():
    schema = _schema()

    def public_or_internal(path):
        return path.startswith((CANONICAL_PUBLIC_PREFIX, INTERNAL_PREFIX))

    def mobile(path):
        return path.startswith(CANONICAL_PUBLIC_PREFIX) and not path.startswith("/api/v1/admin/")

    def dashboard(path):
        return path.startswith("/api/v1/admin/")

    return {
        "openapi.json": _filtered_schema(
            schema,
            title="Baraq Backend API",
            description="Canonical public and internal Baraq API contract generated from Django.",
            include_path=public_or_internal,
        ),
        "mobile-api.json": _filtered_schema(
            schema,
            title="Baraq Mobile API",
            description="Canonical API surface for the Baraq mobile client. Admin and internal endpoints are excluded.",
            include_path=mobile,
        ),
        "dashboard-api.json": _filtered_schema(
            schema,
            title="Baraq Dashboard API",
            description="Canonical RBAC-protected API surface for the Baraq dashboard.",
            include_path=dashboard,
        ),
    }


def _serialized(contract):
    return (json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--check", action="store_true", help="Fail if committed contracts differ from generated output.")
    args = parser.parse_args()

    contracts = build_contracts()
    outdated = []
    for name, contract in contracts.items():
        target = args.output_dir / name
        content = _serialized(contract)
        if args.check:
            if not target.exists() or target.read_bytes() != content:
                outdated.append(target)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        print(f"Exported {target.relative_to(ROOT)} ({len(contract['paths'])} paths)")

    if outdated:
        for target in outdated:
            print(f"OUTDATED: {target.relative_to(ROOT)}")
        return 1
    if args.check:
        print("API contracts are current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
