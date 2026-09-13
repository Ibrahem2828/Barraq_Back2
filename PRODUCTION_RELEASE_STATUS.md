# Backend Production Release Status

Verified 2026-09-13, against a clean local run (isolated SQLite DB, no
production credentials touched -- see "How this was verified" below). This
document supersedes prior status docs where they disagree (`docs/
PRODUCTION_READINESS.md`, `docs/BACKEND_PRODUCTION_READINESS_AUDIT_AR.md`,
`docs/PRODUCTION_BASELINE.md`, `docs/VALIDATION_REPORT.md`,
`docs/RELEASE_CHANGELOG.md`) -- several of those predate fixes already in
this tree (e.g. HMAC nonce/replay protection, which is implemented and now
has dedicated regression tests) or describe gaps that are no longer real.

## Result: CONDITIONAL GO

Conditional on: rotating the leaked database password (see Security below)
and deploying via the root `compose.yaml` (not this repo's own standalone
`docker-compose.yml`, which lacks the `critical`-queue worker described
below). No other launch-blocking issue was found in this pass.

## What was verified, and how

A dedicated venv (`.venv`, gitignored) was created and `requirements/
development.txt` installed. Every command below ran against an isolated
throwaway SQLite database and a Django `LocMemCache` (`config.
settings_dev_local` + explicit environment variables), which `django-environ`
never lets override anything already set in the process's own environment --
so the real `.env` file's live production database credential was never
read, connected to, or transmitted anywhere.

| Gate | Command | Result |
|---|---|---|
| Tests | `python manage.py test --noinput` | **220 passed**, 0 failed |
| Lint | `ruff check .` | **All checks passed** |
| Type check | `mypy apps config` | **Success: no issues found in 198 source files** |
| Migrations | `makemigrations --check --dry-run` | **No changes detected** |
| Migrations | `migrate --noinput` (zero to head) | **Applied cleanly**, all 21 apps |
| OpenAPI schema | `spectacular --validate --fail-on-warn` | **Exit 0**, zero warnings |
| Deploy checks | `check --deploy` (DEBUG=True) | Only the 5 expected DEBUG-mode warnings (HSTS/SSL-redirect/cookie-secure, all `not DEBUG`-gated in `config/settings.py`) |
| Secret/package hygiene | `python scripts/validate_release.py` | **264 files checked, 0 errors** |
| Docker build | -- | **NOT run** -- no Docker daemon available in this environment. Dockerfile reviewed statically (see Docker section); build must be verified in CI or on the deploy host before first use. |

Not independently re-verified this pass (no live Postgres/Redis available
here): connection-pool behavior under `DATABASE_CONN_MAX_AGE`, real SMTP
delivery, real Celery worker/beat process startup. These are exercised by
the new `.github/workflows/ci.yml` (real Postgres 16 + Redis 7 service
containers) on every push/PR going forward.

## Changes made this pass

- **`DELETE /api/v1/users/me/`** (self-service account deletion) --
  `apps/users/views.py`, `apps/users/services.py::delete_user_account`.
  Soft-deletes and anonymizes the account (email/name/phone cleared,
  deterministic tombstone email so it's idempotent), revokes every
  outstanding refresh token, and relies on the existing soft-delete-aware
  user manager + SimpleJWT's per-request `get_user()` re-fetch so even an
  unexpired access token stops working immediately. 6 new tests in
  `apps/users/tests.py::AccountDeletionTests`.
- **Celery queue separation** -- `config/settings.py` (`CELERY_TASK_ROUTES`,
  `CELERY_TASK_DEFAULT_QUEUE`), a new `backend-worker-critical` service in
  `compose.yaml`/`deployment/compose.yaml` consuming only the `critical`
  queue (registration OTP, password reset email), so a burst of AI-dispatch
  work on `default` can never delay it. 2 new tests
  (`apps/users/tests.py::CeleryQueueRoutingTests`).
- **HMAC V2 edge-case tests** -- `apps/ai_integration/tests.py::
  HMACV2InboundVerificationTests`: clock-skew rejection (both directions),
  nonce reuse rejection, and fail-closed behavior when the replay cache
  itself is unavailable. The implementation (`apps/ai_integration/
  security.py`) was already correct; it had no dedicated tests for these
  specific properties before.
- **`FRONTEND_PASSWORD_RESET_URL`** -- already environment-configurable (no
  code change needed); `.env.example`'s documented production value updated
  from the mobile-only `baraq://reset-password` deep link to
  `https://web.baraqapp.com/ar/reset-password`, matching the web app's own
  reset-password route contract (`?uid=&token=`).
- **10 mypy `[var-annotated]` fixes** across `apps/{analytics,audio,
  summaries,study_plans,quizzes,ai_integration,sources}/serializers.py` --
  explicit type annotations on `SlugRelatedField` assignments (pre-existing
  code from the project-scoping work already in this tree; no behavior
  change).
- **`.env.example`** (both this file and the root `D:\baraaq\.env.example` /
  `deployment/.env.example`) -- corrected stale "single container, no
  bundled Postgres/Redis" narrative to match the real multi-container
  `compose.yaml`; added REQUIRED/OPTIONAL section headers to the root file;
  clarified which of the two env-contract files is authoritative for a real
  deployment (root `compose.yaml`) versus a standalone run (this file).
- **`.github/workflows/ci.yml`** (new) -- lint, mypy, migration check,
  `migrate`, `check --deploy`, OpenAPI schema validation, full test suite,
  and the secret-hygiene scanner, against real Postgres 16 + Redis 7 service
  containers, on every push/PR.
- Removed a stray, gitignored `.venv-local-test/` directory left over from
  earlier local testing (not tracked by git; was making
  `scripts/validate_release.py` report false-positive errors from
  third-party package source inside it).

## Security

- **Confirmed**: `.env` is excluded from git (`.gitignore`, never in history)
  and from the Docker build context (`.dockerignore`). `.env.example`
  contains placeholders only.
- **Action still required, not fixable from inside this repo**: `.env`
  contains a live production PostgreSQL password in plaintext, with the
  file's own comment reading "ROTATE THIS." **Rotate this credential before
  relying on this environment for anything.** This file's `ALLOWED_HOSTS`/
  `CSRF_TRUSTED_ORIGINS` point at an `sslip.io`/punycode host, not
  `api.baraqapp.com` -- it looks like a stale staging credential set, not
  the file actually used by the current `compose.yaml` deployment, but
  rotate it regardless of that.
- HMAC V2 (backend <-> AI): SHA-256, canonical request signing over
  service/key/timestamp/nonce/method/path+query/body-hash, clock-skew
  bounded (`BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS`), nonce-based replay
  protection via the Django cache (fails closed if the cache backend
  errors), constant-time comparison throughout. Verified interoperable with
  the AI service's own implementation via `Baraq_AI/scripts/
  validate_django_hmac_interop.py` (imports both real implementations, no
  mocks): **PASS**.
- Account deletion: verified a user cannot authenticate again after
  deletion, with the *same* still-unexpired access token used to delete
  it -- not just on a fresh login attempt.
- No payment-provider integration exists (`PAYMENTS_ENABLED=False` by
  design); no code path reads that flag to gate anything, it is metadata
  only. This remains a product decision, not a bug.

## Database / migrations

21 Django apps, all migrations apply cleanly from zero to head against a
fresh database (verified). `makemigrations --check` reports no drift
between models and migrations. No `RunSQL`/raw-SQL migrations exist. No
squashing needed (largest app has 5 migration files).

## Docker

`Dockerfile`: multi-stage (`builder` installs wheels, `runtime` installs
from them only -- no compiler toolchain in the final image), non-root user
(`baraq`, uid 10001), `tini` as PID 1, `HEALTHCHECK` against `/api/v1/
health/live/`, `.dockerignore` excludes `.env`/`.env.*`/`.git`. Not rebuilt
this pass (no Docker daemon in this environment) -- verify with `docker
build .` before first deploy of this revision.

## Remaining non-blocking items

- `gunicorn.conf.py`'s `forwarded_allow_ips = "*"` trusts any upstream's
  `X-Forwarded-*` headers -- safe today because the port is never published
  directly (only `expose`d on the private network), but fragile if that
  topology ever changes. Consider pinning to the gateway's actual address.
- No self-service payment/checkout flow -- confirmed intentional
  (`PAYMENTS_ENABLED=False`), not attempted this pass; a real launch
  decision, not a code defect.
- Transactional emails (OTP, password reset) are plain-text with no HTML
  template -- cosmetic, not a blocker.
