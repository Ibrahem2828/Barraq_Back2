# Rollback plan

## Preconditions before promotion

1. Record the complete currently running image digest set and Compose revision.
2. Verify a timestamped database backup can be restored to an isolated target.
3. Record applied Django migration IDs and AI Alembic revision.
4. Keep the previous Web/Dashboard/Website/Backend/AI immutable images
   available until the rollback window closes.
5. Do not perform a migration that cannot be safely rolled back or compensated
   without an approved data plan.

## Trigger conditions

Roll back immediately on any of the following:

- Gateway/API/Web/Dashboard availability failure after recreate.
- Public reachability of AI API or a network-boundary violation.
- Authentication, logout, authorization, tenant-scope, or HMAC failure.
- Migration failure, worker crash loop, queue backlog without progress, or
  persistent 5xx/tracebacks.
- OTP delivery/handoff failure for the controlled candidate account.
- Source/AI result corruption, cross-project data exposure, or unbounded cost.

## Procedure

1. Freeze further candidate promotion and preserve relevant logs/metrics without
   copying credentials or private source contents.
2. If no irreversible migration has been applied, recreate affected services
   from the recorded previous immutable digests.
3. If a migration has been applied, follow its documented rollback/compensation
   plan first. Do not run blind down-migrations on production data.
4. Restore the verified backup only if the incident requires data recovery and
   the recovery decision is authorized by the data owner.
5. Re-run the minimal public and internal smoke checks on the restored version.
6. Record incident time, image digests, migration state, observed symptom, and
   recovery result; then block further promotion until root cause is fixed.

## Explicit non-actions

- Do not use `git reset --hard`, mutable image tags, or an ad-hoc source build.
- Do not delete volumes, queues, databases, or object storage to make health
  checks look green.
- Do not disable HMAC, CSRF, authentication, tenant scope, or TLS controls.
