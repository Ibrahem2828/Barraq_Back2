"""Let an organization manager add accounts to their own organization.

`users.create` was seeded in 0002 but held by no scoped role, and no view
used it. The dashboard's create-user endpoint now requires it and confines
a scoped holder to organizations inside its scope, so the organization
manager gets it here. Additive and idempotent: a deployment where the role
or permission is missing (or was deliberately deactivated) is left alone.
"""

from django.db import migrations

ROLE = "organization_manager"
PERMISSION = "users.create"


def grant(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")
    role = AdminRole.objects.filter(code=ROLE).first()
    permission = AdminPermission.objects.filter(code=PERMISSION).first()
    if role is not None and permission is not None:
        role.permissions.add(permission)


def revoke(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")
    role = AdminRole.objects.filter(code=ROLE).first()
    permission = AdminPermission.objects.filter(code=PERMISSION).first()
    if role is not None and permission is not None:
        role.permissions.remove(permission)


class Migration(migrations.Migration):
    dependencies = [
        ("admin_dashboard", "0002_seed_default_rbac"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
