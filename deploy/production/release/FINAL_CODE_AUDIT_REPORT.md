# Final code audit report

## Scope and verdict

The tracked release code is **CONDITIONALLY READY FOR MANUAL UPLOAD**. No
known P0/P1 source, contract, or static topology defect remains in the audited
repositories. The conditional status reflects unexecuted real-stack gates and
an out-of-repository recovery artifact; it does not claim production proof.

## Architecture verified in source

`Browser → Next.js BFF → Django → private FastAPI AI → worker/provider`.

The Caddy configuration has no `reverse_proxy ai-api` route. AI API and worker
services remain private-only in the canonical Compose topology. Backend↔AI
HMAC v2, project/source ownership, callback authentication, and source scope
are covered by Backend/AI tests and contract checks.

## Historical-incident disposition

| # | Incident class | Disposition |
| --- | --- | --- |
| 1 | Gateway/API/Dashboard 502 after recreate | EXTERNAL RELEASE GATE — static topology tested; recreate needs candidate Docker stack |
| 2 | Dashboard body disappearing | FIXED/COVERED — production Dashboard build and browser access suite pass |
| 3 | Login error contract 400 vs 401 | FIXED/COVERED — Backend/Web contract and auth tests pass |
| 4 | Visible autofill did not submit | FIXED/COVERED — 5 real-browser autofill regressions pass |
| 5 | OTP critical worker egress | EXTERNAL RELEASE GATE — topology is private + backend-egress; real SMTP/DNS proof pending |
| 6 | Public AI route | FIXED THIS CLOSURE — explicit former-host 404 and regression tests |
| 7 | Image/source drift | EXTERNAL RELEASE GATE — immutable SHA/digest procedure documented |
| 8 | Worker/API drift | EXTERNAL RELEASE GATE — image parity and candidate worker startup required |
| 9 | Logout did not revoke session | FIXED/COVERED — authenticated Web browser lifecycle passes |
| 10 | Permission/scope cross-product escalation | FIXED/COVERED — Backend tenant/RBAC tests and Dashboard E2E pass |
| 11 | UNRESTRICTED/NO_ACCESS fail-open | FIXED/COVERED — scope tests assert fail-closed behavior |
| 12 | Unsafe migration-history rewrite | FIXED/COVERED — tracked migrations and migration checks pass; no history rewrite performed |
| 13 | Join frontend/backend contract mismatch | FIXED/COVERED — current contracts and API tests pass |
| 14 | Navigation groups hide routes only | FIXED/COVERED — direct backend authorization and Dashboard route tests pass |
| 15 | Next standalone mismatch | FIXED/COVERED — production builds and start-based browser tests pass |
| 16 | `unsafe-eval` | FIXED/COVERED — Web CSP regression/header check passes |
| 17 | Security-header inconsistency | FIXED/COVERED — Web and Dashboard header gates pass |
| 18 | DRF body-limit bypass | FIXED/COVERED — current Backend validation suite passes |
| 19 | Beat impossible HTTP health | FIXED/COVERED — canonical topology tests cover disabled inappropriate worker probes |
| 20 | Production LocMem cache | FIXED/COVERED — production startup guard is present and tested |
| 21 | Stale docs/contracts | FIXED/COVERED — generated contracts are current; this release set supersedes older reports |
| 22 | Permanent loader routes | FIXED/COVERED — browser route/session suites pass |
| 23 | AI worker/provider networking | EXTERNAL RELEASE GATE — static `provider-egress`; candidate provider/worker proof pending |
| 24 | Previous secret exposure | PARTIALLY FIXED — current source scan is clean and hardcoded demo credentials removed; rotate any historical/operational values before release |

## Contract and source lifecycle review

- `POST source/process` uses the confirmed asynchronous 202 response contract.
- Source capability evaluation treats eligible uploaded sources as usable while
  excluding failed/unsupported sources.
- Effective upload limits and supported file types are backend-authoritative.
- AI jobs carry immutable source scope; excessive selection is rejected rather
  than silently truncated.
- Sada materializes a reusable transcript file; downstream source use and
  callback idempotency have dedicated tests.
- Ingestion identity includes source/content/project scope; retrieval isolation
  and version invalidation are tested.
- Public job progress maps real backend stages instead of client-only fake
  percentages.

## Security review summary

PASS in code: authenticated BFF transport, CSRF/session flow, refresh-token
revocation, backend authorization, admin scopes, IDOR defenses, HMAC request
and callback checks, private AI routing, format/size validation, error
envelopes, and no current high-confidence secret literal scan hits.

OPEN release gates: target-host TLS/Caddy behavior, external SMTP delivery,
provider behavior/cost boundaries, real PostgreSQL/pgvector/Redis/Celery,
container-image provenance, backup/restore, and live observability.

## Non-release workspace artifacts

The workspace contains historical copies/scratch directories and a recovery
artifact outside all release repositories. The upload operator must package
only the five repositories listed in `RELEASE_CANDIDATE_MANIFEST.md`; no root
directory archive is permitted.

