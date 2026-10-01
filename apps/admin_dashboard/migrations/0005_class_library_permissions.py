"""Permissions for the Classroom Shared Library.

Creates `library.view` and `library.manage` and grants both to the
organization manager and the class supervisor -- their reach stays bounded by
each assignment's scope -- and to the super_admin role, whose definition is
"every permission" (a superuser account resolves them dynamically anyway).
Additive and idempotent; reversing removes only these two permissions.
"""

from django.db import migrations

PERMISSIONS = (
    ("library.view", "Class library", "View the classroom shared library"),
    ("library.manage", "Class library", "Upload and manage classroom shared library files"),
)
ROLES = ("super_admin", "organization_manager", "class_supervisor")


def grant(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")
    created = []
    for code, category, name in PERMISSIONS:
        permission, _ = AdminPermission.objects.get_or_create(
            code=code, defaults={"name": name, "category": category, "is_active": True}
        )
        created.append(permission)
    for role in AdminRole.objects.filter(code__in=ROLES):
        role.permissions.add(*created)


def revoke(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminPermission.objects.filter(code__in=[code for code, _, _ in PERMISSIONS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("admin_dashboard", "0004_grant_students_view_to_school_roles"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
