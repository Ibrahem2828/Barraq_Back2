# Manual deployment checklist

This checklist prepares a candidate only. It does not authorize an automatic
deployment and it must be executed by an operator with the appropriate target
environment access.

## 1. Freeze and package source

- [ ] Use only the five repositories in `RELEASE_CANDIDATE_MANIFEST.md`.
- [ ] Confirm `git status --porcelain=v2` is empty for each repository.
- [ ] Record full `git rev-parse HEAD` values and the Git remote revision.
- [ ] Do not upload workspace-root scratch folders, ignored virtual
  environments, local databases, test reports, `.env` files, or recovery data.
- [ ] Use only `Baraaq_back/backend/deploy/production/compose.yaml` and
  `Caddyfile`; reject loose root-level Compose/Caddy copies.

## 2. Secrets and configuration

- [ ] Create/update production values through the approved secret manager,
  never through a committed file or shell transcript.
- [ ] Verify required variables exist by name only: Django secret/JWT keys,
  database/Redis URLs, HMAC key set/current key ID, SMTP settings, storage,
  Sentry, provider credentials, public hostnames, and immutable image tags.
- [ ] Set `APP_ENV=production`, `DEBUG=false`, production cache/Redis URLs,
  trusted hosts/origins, secure cookies, and internal AI URLs exactly as the
  canonical README describes.
- [ ] Complete `SECRET_ROTATION_CHECKLIST.md` before first public traffic.

## 3. Immutable candidate build

- [ ] Build or pull Backend, AI API/worker/beat, Web, Dashboard, and Website
  from frozen source SHA tags.
- [ ] Record every resulting registry digest.
- [ ] Ensure the same AI digest serves API, worker, and beat roles where the
  Compose file specifies a shared image.
- [ ] Ensure the same Backend digest serves API, worker, critical worker, and
  beat roles where the Compose file specifies a shared image.
- [ ] Never use `latest`, mutable branch tags, or a target-host source build.

## 4. Candidate configuration validation

- [ ] In the canonical deploy directory run `docker compose config --quiet`.
- [ ] Confirm only Gateway has loopback/published bindings; PostgreSQL, Redis,
  workers, and AI API have no public port.
- [ ] Confirm `private` is internal, critical worker has `backend-egress`, and
  AI worker has `provider-egress` only as required.
- [ ] Validate/load Caddy and confirm the former AI hostname returns 404 rather
  than proxying internally.
- [ ] Start the candidate with immutable images; do not point it at production
  data for the first boot.

## 5. Data/migrations/queues

- [ ] Take and verify a restorable backup before any production migration.
- [ ] Run Django and AI migrations in the documented one-shot migration
  services; capture only status/output without secrets.
- [ ] Verify PostgreSQL/pgvector, Redis, Celery workers, beat, and critical
  queue health with candidate-only data.
- [ ] Prove OTP dispatch reaches the critical queue and SMTP handoff boundary.

## 6. Promote only after smoke

- [ ] Complete every item in `POST_DEPLOY_SMOKE.md`.
- [ ] Capture image digests, migration IDs, smoke results, and rollback target.
- [ ] Stop and roll back on unexpected 5xx, traceback, restart loop,
  authorization leak, or private-AI exposure.

