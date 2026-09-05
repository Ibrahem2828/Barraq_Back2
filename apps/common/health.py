from __future__ import annotations

import uuid

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import DatabaseError, connection


def database_status():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return "ok"
    except DatabaseError:
        return "error"


def cache_status():
    try:
        key = f"baraq:health:{uuid.uuid4().hex}"
        cache.set(key, "ok", 10)
        healthy = cache.get(key) == "ok"
        cache.delete(key)
        return "ok" if healthy else "error"
    except Exception:  # noqa: BLE001 -- a readiness probe must report "error", not crash on any backend fault
        return "error"


def storage_status():
    name = f"healthchecks/{uuid.uuid4().hex}.txt"
    try:
        stored_name = default_storage.save(name, ContentFile(b"ok"))
        healthy = default_storage.exists(stored_name)
        default_storage.delete(stored_name)
        return "ok" if healthy else "error"
    except Exception:  # noqa: BLE001 -- a readiness probe must report "error", not crash on any backend fault
        return "error"


def readiness_payload():
    checks = {
        "database": database_status(),
        "cache": cache_status(),
        "storage": storage_status(),
    }
    return checks, all(value == "ok" for value in checks.values())
