# Production deployment — source of truth

This directory is the authoritative production configuration for the whole
Baraq platform. Until it was added, `compose.production.fixed.yaml` and a
`Caddyfile` lived as loose, untracked files on one machine: the deployment
could not be reproduced from any repository, and the one difference between
that file and its untracked sibling (`docker/compose.yaml`) was the fix that
lets registration OTP email leave the network at all. A release-blocking
state, so it is recorded here.

## Files

| File | Purpose |
|---|---|
| `compose.yaml` | Full-stack production stack (`name: baraq-production`), 16 services |
| `Caddyfile` | Edge routing and request-body limits for every public host |

Not to be confused with the repository root's `docker-compose.yml`
(`name: baraq-backend`), which is the **backend-only** stack for local and
single-service work. Both are legitimate; they have different scopes.

## Secrets

This directory contains **no secret values**. Every credential is a
`${VAR:?...}` reference that fails fast when unset. Supply them from the
deployment host's own environment or secret store — never commit a value, and
never add a `.env` here.

Required variable **names** (values live outside the repository):

```
AI_DB_PASSWORD            BACKEND_DB_PASSWORD       BACKEND_SECRET_KEY
EMAIL_HOST                EMAIL_HOST_PASSWORD       EMAIL_HOST_USER
EMAIL_PORT                EMAIL_USE_SSL             EMAIL_USE_TLS
DEFAULT_FROM_EMAIL        HMAC_KEY_ID               HMAC_SHARED_SECRET
OPENAI_PRIMARY_API_KEY    REDIS_PASSWORD            SENTRY_DSN
WAITLIST_ADMIN_PASSWORD   WAITLIST_ADMIN_USERNAME   WEB_SENTRY_DSN
AI_VERSION                BACKEND_VERSION           WEB_VERSION
DASHBOARD_IMAGE_TAG       WEBSITE_IMAGE_TAG         HOST_PORT
```

## Network topology

Four networks, and the isolation between them is a security control, not a
convenience:

| Network | Internal? | Purpose |
|---|---|---|
| `edge` | no | Gateway ↔ the four public-facing apps |
| `private` | **yes** | Everything internal: databases, Redis, workers, AI API |
| `backend-egress` | no | Outbound-only, so `backend-worker-critical` can reach SMTP |
| `provider-egress` | no | Outbound-only, so `ai-worker` can reach AI providers |

Only `gateway` publishes ports, all bound to `127.0.0.1` so a reverse proxy
or tunnel terminates TLS in front of it. PostgreSQL, Redis, every Celery
worker and the AI API publish nothing and sit on the internal network.

**`ai-api` is on `private` only.** The browser reaches AI through
Next.js → Django → the private AI service; Caddy has no route to it. Do not
add one to simplify a health check.

`backend-worker-critical` needs both `private` (Redis, database) and
`backend-egress` (SMTP). Dropping the second is what silently breaks
registration OTP: the worker resolves nothing, every send fails at DNS, and
the task retries into exhaustion while the student waits.

## Upload size chain

Each hop must clear the one behind it, or a file is refused at an invisible
limit after the user was told a larger one:

```
Caddy request_body 64MB  ->  BFF_MAX_BODY_BYTES 55MB
  ->  Django STUDENT_SOURCE_MAX_UPLOAD_MB 50MB  ->  AI max_source_file_bytes 50MB
```

The 50MB figure is bounded by the AI service, which reads a whole source into
memory during ingestion. Raising it means making that path stream first.

## Validation

`docker compose config --quiet` is the authoritative check and is an RC gate;
it could not be run where this was written (no Docker CLI). YAML validity and
the port/network assertions above are covered by
`apps/common/test_deployment_config.py`, which runs in the normal test suite.
