# Baraq release-candidate manifest

Validation date: 2026-09-21 (local engineering closure).

## Verdict

**CONDITIONALLY READY FOR MANUAL UPLOAD.**

The version-controlled code, contracts, static deployment topology, and local
browser/API checks are green. A manual upload is allowed only after every item
in `MANUAL_DEPLOYMENT_CHECKLIST.md` and `POST_DEPLOY_SMOKE.md` is completed
against the target environment. This is not a production deployment record.

## Canonical release inputs

| Component | Repository path | Code snapshot SHA |
| --- | --- | --- |
| Backend + canonical deployment files | `Baraaq_back/backend` | `68e9000c47caaba4813f415792c6cc9f6d638bdc` |
| AI service | `Baraq_AI` | `7c1260333f94a726c91a65d0d07c794177daaaad` |
| Student Web | `web` | `c87ae2059110549e50cc9bec20a8712f9ab7a26d` |
| Admin Dashboard | `Baraq_Dashboard_Professional` | `36aa960c304f5b8dc71e506a4cb2eb528b5e433a` |
| Marketing website | `Baraq_Website` | `5b6d4b592beeab2931d28813647a02e414f16c23` |

The Backend SHA above is the source/configuration snapshot immediately before
this document was committed. A file cannot safely contain its own future Git
object ID; therefore the *final* Backend SHA is frozen at upload time with the
commands below and must be recorded in the upload ticket/artifact manifest.

```powershell
git -C Baraaq_back/backend status --porcelain=v2
git -C Baraaq_back/backend rev-parse HEAD
git -C Baraq_AI status --porcelain=v2
git -C Baraq_AI rev-parse HEAD
git -C web status --porcelain=v2
git -C web rev-parse HEAD
git -C Baraq_Dashboard_Professional status --porcelain=v2
git -C Baraq_Dashboard_Professional rev-parse HEAD
git -C Baraq_Website status --porcelain=v2
git -C Baraq_Website rev-parse HEAD
```

All five commands must report a clean worktree and the recorded IDs must be
the exact sources used to build immutable images. Do not substitute branch
names, `latest`, or arbitrary remote `HEAD` values.

## Deployment source of truth

Only these version-controlled files define the production topology:

- `deploy/production/compose.yaml`
- `deploy/production/Caddyfile`
- `deploy/production/README.md`

The loose root-level `deployment/` and `docker/` copies are historical,
unversioned artifacts. They are expressly excluded from release upload and
must not be used as a Compose or Caddy source.

## Release identity and image policy

1. Build every service from the frozen source SHA.
2. Tag every image with its full SHA and record its registry digest.
3. Run candidate validation using those exact image digests.
4. Promote by digest only; never rebuild on the target host.
5. Retain the previous complete digest set until post-deploy smoke and rollback
   window have closed.

## Local evidence summary

- Backend: Django checks, migrations, OpenAPI/contract freshness, Ruff, Mypy,
  release validator, and **491 tests passed** (6 external integrations skipped).
- AI: Ruff, Mypy, and **275 collected tests passed** (5 pgvector-dependent
  tests skipped because no isolated PostgreSQL+pgvector service is local).
- Web: **355 unit tests passed**, typecheck/lint/build/header checks passed,
  and 17 real-browser local tests passed (including 12 authenticated flows).
- Dashboard: **175 unit tests passed**, typecheck/lint/build/header checks
  passed, and 5 real-browser local authorization tests passed.
- Website: syntax check passed.

See `PRE_DEPLOY_VALIDATION_REPORT.md` for scope and external gates.

