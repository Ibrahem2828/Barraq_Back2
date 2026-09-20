"""Give each *historically global* role assignment an explicit GLOBAL scope.

Before this phase an admin's reach was implicitly the whole platform. With
scope introduced, "no scope rows" has to mean *no access* -- otherwise a new
organization-manager assignment that forgets its scope would silently become
global, and a fail-open authorization default is the one bug that never
announces itself.

Making absence mean nothing is only safe if every assignment that *did* have
reach is given it explicitly, which is what this does.

Who qualifies, and why
----------------------
`get_user_admin_permissions` has only ever derived permissions from

    AdminUserRole.is_active = True   AND   AdminRole.is_active = True

so exactly those assignments had platform-wide reach, and exactly those are
backfilled. Three groups are deliberately left with nothing:

* deactivated assignments, and assignments on a deactivated role -- they
  granted no permission before this migration, so granting them global now
  would hand real access to a dormant row the moment someone re-enables it.
  Re-activating one goes through the assignment API instead, which requires
  a scope to be stated.
* `is_staff` accounts with no role assignment. `is_staff` alone has granted
  nothing since the dynamic RBAC work; the flag is set by `User.save()` for
  anyone who can open the dashboard and was never authority in itself.
* learners. They hold no AdminUserRole, so no row here can describe them.

Assignments that already carry any scope are skipped, so re-running this
cannot widen a reach an operator has since narrowed.

This migration is data-only and has never been applied outside local test
databases, which are rebuilt per run.
"""

from django.db import migrations


def grant_global_scope_to_existing_roles(apps, schema_editor):
    AdminUserRole = apps.get_model("admin_dashboard", "AdminUserRole")
    AdminRoleScope = apps.get_model("organizations", "AdminRoleScope")

    # Any existing scope at all, of any type: an assignment an operator has
    # already narrowed to one organization must never be widened by a
    # re-run.
    already_scoped = set(AdminRoleScope.objects.values_list("admin_user_role_id", flat=True))

    historically_global = AdminUserRole.objects.filter(
        is_active=True, role__is_active=True
    ).values_list("id", flat=True)

    AdminRoleScope.objects.bulk_create(
        [
            AdminRoleScope(
                admin_user_role_id=role_id,
                scope_type="global",
                organization=None,
                classroom=None,
            )
            for role_id in historically_global
            if role_id not in already_scoped
        ]
    )


def drop_global_scope(apps, schema_editor):
    """Deliberately a no-op.

    The obvious reverse -- delete every global scope row -- destroys more
    than this migration created: any platform-wide grant an operator has
    issued since would be revoked, silently and with no record of what was
    lost. The rows this leaves behind are exactly the reach those accounts
    already had, so leaving them is both harmless and honest. Reversing
    past 0001 drops the table outright, which is the real undo.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0001_initial"),
        ("admin_dashboard", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(grant_global_scope_to_existing_roles, drop_global_scope),
    ]
