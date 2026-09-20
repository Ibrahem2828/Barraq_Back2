"""Give every existing role assignment an explicit GLOBAL scope.

Before this phase, an admin's reach was implicitly the whole platform. With
scope introduced, "no scope rows" has to mean *no access* -- otherwise a new
organization-manager assignment that forgets its scope would silently become
global, and a fail-open authorization default is the one bug that never
announces itself.

Making absence mean nothing is only safe if every existing assignment is
given the grant it already had, which is what this does. Current platform
admins keep exactly the access they have today; only new assignments are
subject to the stricter default.

Reversible: removing the rows restores the pre-migration table state.
"""

from django.db import migrations


def grant_global_scope_to_existing_roles(apps, schema_editor):
    AdminUserRole = apps.get_model("admin_dashboard", "AdminUserRole")
    AdminRoleScope = apps.get_model("organizations", "AdminRoleScope")

    existing = set(
        AdminRoleScope.objects.values_list("admin_user_role_id", flat=True)
    )
    AdminRoleScope.objects.bulk_create(
        [
            AdminRoleScope(
                admin_user_role_id=role_id,
                scope_type="global",
                organization=None,
                classroom=None,
            )
            for role_id in AdminUserRole.objects.values_list("id", flat=True)
            if role_id not in existing
        ]
    )


def drop_global_scope(apps, schema_editor):
    AdminRoleScope = apps.get_model("organizations", "AdminRoleScope")
    AdminRoleScope.objects.filter(scope_type="global").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0001_initial"),
        ("admin_dashboard", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(grant_global_scope_to_existing_roles, drop_global_scope),
    ]
