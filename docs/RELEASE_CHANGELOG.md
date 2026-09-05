# Release Changelog - Production Candidate 4.0

## 2026-08-19 — Follow-up correction

The "Removed: Embedded AI provider implementations and duplicated AI
gateway/platform code" entry below was aspirational when written: `apps/ai_gateway`
and `apps/ai_platform` were never wired into `INSTALLED_APPS`/`config/urls.py`
(see `docs/BACKEND_PRODUCTION_READINESS_AUDIT_AR.md`), but the directories
themselves (~4,000 lines) were still physically present in the tree. They have
now actually been deleted, along with `AI_PLATFORM_IMPLEMENTATION.md` and
`CHANGELOG_AI_IMPLEMENTATION.md`, which documented only that dead code.

## Removed

- Embedded AI provider implementations and duplicated AI gateway/platform code.
- Real environment files, local databases, media payloads, bytecode, and repository metadata.
- Duplicate quiz recommendation function and stale contracts/documentation.

## Added

- `apps.ai_integration` as the sole adapter to the standalone AI service.
- AI job, feedback, webhook, idempotency, credit, materialization, cancellation, and internal manifest APIs.
- Root landing page, custom 404/500 pages, API root, liveness/readiness checks.
- Notifications, support tickets, summaries, transcriptions, recommendations, and expanded administrative APIs.
- Password reset, logout blacklist, email normalization, throttling, structured exceptions, request tracing.
- Production Dockerfile, Compose stack, Coolify guide, AI integration guide, API contract, and release validator.

## Changed

- Canonical routes are now `/api/v1/`; `/api/` remains a temporary compatibility alias.
- Quiz publishing is explicit and validated; generated/manual draft behavior is separated.
- Attempt timing is enforced by the server and active attempts are reused safely.
- Source processing and AI dispatch are asynchronous.
- Paid entitlement and usage counters are transaction-safe.
- OpenAPI documentation is private by default in production.
