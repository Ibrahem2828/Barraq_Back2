# Admin Dashboard Backend Foundation

This phase adds an additive backend admin platform under `/api/admin/`. Student auth and existing student-facing APIs are unchanged.

## RBAC Model

The admin platform uses custom RBAC models: `AdminPermission`, `AdminRole`, `AdminUserRole`, and `AuditLog`.

`is_superuser` and the `super_admin` role both grant all active admin permissions. `is_staff` remains useful for Django Admin, but `/api/admin/` requires the custom RBAC layer.

## Default Roles

- `super_admin`: all permissions.
- `admin`: dashboard and read-focused platform visibility.
- `content_manager`: education stages, subjects, and quiz moderation.
- `support`: user support and source/collection visibility.
- `finance`: subscription-readiness permissions.
- `analyst`: read-only analytics and core visibility.

## Permission Codes

Dashboard: `dashboard.view`

Users: `users.view`, `users.create`, `users.update`, `users.suspend`, `users.activate`, `users.delete`

Admins: `admins.view`, `admins.create`, `admins.update`, `admins.delete`, `admins.assign_roles`, `admins.assign_permissions`

Roles: `roles.view`, `roles.create`, `roles.update`, `roles.delete`

Students: `students.view`, `students.update`

Content: `education_stages.view`, `education_stages.create`, `education_stages.update`, `education_stages.delete`, `subjects.view`, `subjects.create`, `subjects.update`, `subjects.delete`

Sources: `sources.view`, `sources.delete`, `sources.moderate`, `collections.view`, `collections.delete`, `collections.moderate`

Study: `study_plans.view`, `study_plans.delete`, `study_tasks.view`

Quizzes: `quizzes.view`, `quizzes.moderate`, `quizzes.delete`, `quiz_attempts.view`

Characters: `character_interactions.view`

Analytics: `analytics.view`

Audit: `audit_logs.view`

System: `system.view`, `system.health`, `system.settings`

Subscriptions readiness: `subscriptions.view`, `subscriptions.create`, `subscriptions.update`, `subscriptions.cancel`, `subscription_plans.view`, `subscription_plans.create`, `subscription_plans.update`, `subscription_plans.delete`

## Endpoints

- `GET /api/admin/me/`
- `GET /api/admin/overview/`
- `GET /api/admin/permissions/`
- `GET|POST /api/admin/roles/`
- `GET|PATCH|DELETE /api/admin/roles/{id}/`
- `GET|POST /api/admin/admin-users/`
- `GET|PATCH|DELETE /api/admin/admin-users/{id}/`
- `POST /api/admin/admin-users/{id}/assign-roles/`
- `GET|PATCH /api/admin/users/{id}/`
- `POST /api/admin/users/{id}/suspend/`
- `POST /api/admin/users/{id}/activate/`
- `GET|DELETE /api/admin/sources/{id}/`
- `GET /api/admin/source-collections/`
- `GET /api/admin/study-plans/`
- `GET /api/admin/quizzes/`
- `GET /api/admin/quiz-attempts/`
- `GET /api/admin/character-interactions/`
- `GET /api/admin/audit-logs/`
- `GET /api/admin/system/health/`

All list endpoints use DRF pagination and support `search`, `ordering`, and scoped filters where relevant.

## Key Responses

`/api/admin/me/` returns the authenticated admin user, active roles, sorted permission codes, and `allowed_sections` booleans for dashboard navigation. Students and inactive users receive `403`.

`/api/admin/overview/` requires `dashboard.view` and returns platform counts, weekly activity, subscription readiness count as `0`, and safe system health fields.

## Demo Accounts

`python manage.py seed_demo_data` ensures RBAC permissions and default roles exist, then creates or updates demo users safely:

- `admin@baraq.app / Admin@123456`: Super Admin.
- `project.admin@baraq.app / ProjectAdmin@123456`: limited Admin role.
- `student@baraq.app / Student@123456`: student.

Existing demo passwords are not reset unless `--reset-demo-passwords` is passed.

## Security Rules

- Students cannot access `/api/admin/`.
- Admins need the exact permission for each endpoint/action.
- Normal admins cannot modify or delete Super Admin users.
- Normal admins cannot assign `super_admin` or roles containing permissions they do not have.
- The last Super Admin cannot be deleted.
- Admins cannot delete themselves through `/api/admin/admin-users/`.
- Passwords and tokens are never returned.
- Sensitive admin changes write `AuditLog` entries.

## Dashboard Integration Notes

The frontend dashboard should call `/api/admin/me/` after login, render navigation from `allowed_sections`, and use permission-specific empty states for `403` responses. Subscription management is now available as an internal/manual system without payment-provider integration.

## Subscriptions Core

Phase Admin-2 adds manual subscription management under `/api/admin/subscription-plans/`, `/api/admin/user-subscriptions/`, and `/api/admin/subscription-usage/`. User-level actions are available at `/api/admin/users/{id}/change-subscription/` and `/api/admin/users/{id}/cancel-subscription/`.

Finance and Super Admin roles can view subscription data through the existing RBAC permissions. See `SUBSCRIPTIONS.md` for limits, student endpoints, and enforcement behavior.
