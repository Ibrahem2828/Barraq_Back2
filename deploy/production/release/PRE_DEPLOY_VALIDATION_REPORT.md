# Pre-deploy validation report

Validation date: 2026-09-21. Scope: local codebase and isolated test services
only. No production host, DNS, mailbox, provider account, database, Redis, or
container runtime was accessed.

## Local gates that passed

| Area | Command or evidence | Result |
| --- | --- | --- |
| Backend correctness | `manage.py check`, migrations check after isolated migration, `manage.py test` | PASS — 491 tests, 6 expected integration skips |
| Backend quality/contracts | Ruff, Mypy, Spectacular validation, contract freshness, release validator | PASS |
| AI correctness | Ruff, Mypy, full pytest with isolated temp area | PASS — 275 collected, 5 expected pgvector skips |
| Student Web | Vitest, typecheck, ESLint, production build, header check | PASS — 355 tests |
| Dashboard | Vitest, typecheck, ESLint, production build, header check | PASS — 175 tests |
| Website | `npm run check` | PASS |
| Browser E2E: Web | production Next build + local isolated Django | PASS — 5 autofill and 12 smoke/authenticated tests |
| Browser E2E: Dashboard | production Next build + local isolated Django fixtures | PASS — 5 authorization/RTL tests |
| Static deployment topology | `apps.common.test_deployment_config` within Backend suite | PASS |
| Current-tree high-confidence secret scan | Git-tracked source scan, filenames only | PASS — no high-confidence literals found |

Expected test logs include deliberately generated 400/401/403/404/409 cases
used to prove rejection paths. They are not unexpected server failures.

## Fixed during this closure

| Finding | Fix | Verification |
| --- | --- | --- |
| Old public AI hostname risk | Former AI hostname explicitly returns 404; Caddy never proxies to `ai-api` | deployment topology regression test |
| Web build depended on remote font fetches | system-font stack and no remote Google font/CSP allowances | production build + font regression + header check |
| Demo seed command embedded/default-logged credentials | explicit demo-only opt-in and runtime environment inputs; no password output | focused command tests + current-tree scan |
| AI dependency floors allowed audit findings | fixed floors for cryptography/Pillow/pytest; compatible pytest-asyncio; CI audits installed environment | dependency-policy test + full AI gates |

## Mandatory external release gates

These are intentionally **not** marked passed. They need an isolated candidate
environment with real deployment tooling and non-production test credentials.

1. `docker compose -f deploy/production/compose.yaml config --quiet` using the
   target secret manager values (do not print rendered environment values).
2. Build or pull every immutable SHA-tagged image and record each digest.
3. Start the full candidate stack and prove gateway/API/Web/Dashboard
   availability after a deliberate recreate; inspect service logs for 5xx,
   tracebacks, and restart loops.
4. Run PostgreSQL migrations and the AI Alembic migration against a disposable
   PostgreSQL+pgvector candidate; prove forward migration and documented
   rollback implications before production data is touched.
5. Verify Redis connectivity, Celery workers, beat scheduling, critical OTP
   queue routing, and bounded retry behavior on the real network topology.
6. Send an OTP to a controlled mailbox; separately prove task acceptance,
   SMTP handoff, DNS/egress, and mailbox receipt without logging the OTP.
7. Execute private Backend↔AI HMAC v2 dispatch, callback replay protection,
   source download, and provider mock/live credential probes. AI remains
   private throughout.
8. Parse/load the actual Caddy candidate configuration and verify public
   hosts, TLS, headers, request-size limits, and former AI host denial.
9. Run the dependency advisory audit from CI or a network-enabled runner. The
   local advisory client timed out after remediation, so it is not evidence of
   a clean live advisory feed.
10. Perform backup restore, rollback rehearsal, load baseline, log/metric and
    alert verification.

## Manual-upload exclusion

Do not upload the workspace root wholesale. In particular, the recovery-code
artifact reported in `SECRET_ROTATION_CHECKLIST.md` is outside all release
repositories and must stay out of archives, images, copied directories, logs,
and support bundles.
