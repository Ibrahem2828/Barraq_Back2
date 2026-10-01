"""Let school staff follow their students' performance.

`students.view` was seeded in 0002 but held by no scoped role and used by no
view. The student-performance endpoint now requires it and reports only on
students inside the caller's organization or class scope, so the
organization manager and the class supervisor get it here. Additive and
idempotent, reversible.
"""

from django.db import migrations

ROLES = ("organization_manager", "class_supervisor")
PERMISSION = "students.view"


def grant(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")
    permission = AdminPermission.objects.filter(code=PERMISSION).first()
    if permission is None:
        return
    for role in AdminRole.objects.filter(code__in=ROLES):
        role.permissions.add(permission)


def revoke(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")
    permission = AdminPermission.objects.filter(code=PERMISSION).first()
    if permission is None:
        return
    for role in AdminRole.objects.filter(code__in=ROLES):
        role.permissions.remove(permission)


class Migration(migrations.Migration):
    dependencies = [
        ("admin_dashboard", "0003_grant_users_create_to_organization_manager"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
