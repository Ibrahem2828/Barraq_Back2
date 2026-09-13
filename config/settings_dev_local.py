"""
Phase-2 LOCAL INTEGRATION TESTING ONLY. Not part of the deployed backend, not
referenced by any Dockerfile/compose file, safe to delete at any time.

Purpose: let `web/` be integration-tested against a *real* Django instance
instead of only source-reading, without touching the production database
credential that was sitting in this directory's `.env` (see
web/docs/BACKEND_INTEGRATION_STATUS.md for the disclosure) and without
requiring a locally-installed Redis for cache/Celery.

Usage: `DJANGO_SETTINGS_MODULE=config.settings_dev_local`, alongside the
environment variables set in `run_local_dev_server.ps1` (also test-harness
only). Every other setting is inherited unchanged from `config.settings`.
"""

from .settings import *  # noqa: F401,F403

# Runs Celery tasks (password-reset email, source text extraction, AI job
# dispatch stub) synchronously in-process. No broker/worker required.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
