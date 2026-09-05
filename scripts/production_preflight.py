#!/usr/bin/env python3
"""Run safe production readiness checks without exposing configuration secrets."""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _result(name, passed, detail=""):
    state = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"{state}: {name}{suffix}")
    return passed


def _check_database_and_migrations():
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    connection = connections["default"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return len(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-database", action="store_true")
    parser.add_argument("--skip-cache", action="store_true")
    parser.add_argument("--skip-storage", action="store_true")
    parser.add_argument("--skip-deploy-checks", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        import django

        django.setup()
    except Exception as exc:  # noqa: BLE001 -- preflight must report any startup fault, not crash uncaught
        return 0 if _result("Django startup and environment validation", False, type(exc).__name__) else 1

    from django.conf import settings
    from django.core.cache import cache
    from django.core.management import call_command

    from apps.common.health import storage_status

    failures = 0
    failures += not _result("production mode", not settings.DEBUG, f"environment={settings.ENVIRONMENT}")
    failures += not _result("public API origin", settings.PUBLIC_API_BASE_URL.startswith("https://"))
    failures += not _result("AI configuration", not settings.AI_SERVICE_ENABLED or settings.AI_SERVICE_TIMEOUT_SECONDS > 0)

    if not args.skip_deploy_checks:
        captured = io.StringIO()
        try:
            call_command("check", deploy=True, stdout=captured, stderr=captured)
            warnings = "WARNINGS:" in captured.getvalue()
            _result("Django deployment checks", True, "warnings require review" if warnings else "no warnings")
        except Exception as exc:  # noqa: BLE001 -- preflight must report any check fault, not crash uncaught
            failures += not _result("Django deployment checks", False, type(exc).__name__)

    if not args.skip_database:
        try:
            pending = _check_database_and_migrations()
            failures += not _result("database connection", True)
            failures += not _result("migration state", pending == 0, f"pending={pending}")
        except Exception as exc:  # noqa: BLE001 -- preflight must report any check fault, not crash uncaught
            failures += not _result("database and migration state", False, type(exc).__name__)

    if not args.skip_cache:
        try:
            key = "baraq:preflight"
            cache.set(key, "ok", 10)
            healthy = cache.get(key) == "ok"
            cache.delete(key)
            failures += not _result("cache", healthy)
        except Exception as exc:  # noqa: BLE001 -- preflight must report any check fault, not crash uncaught
            failures += not _result("cache", False, type(exc).__name__)

    if not args.skip_storage:
        storage = storage_status()
        failures += not _result("storage", storage == "ok", storage)

    print(f"Preflight result: {'PASS' if failures == 0 else 'FAIL'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
