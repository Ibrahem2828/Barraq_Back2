"""Permissions for صفي (Class Work): announcements, calendar, assignments
and teacher-made quizzes.

Creates `class_work.view` and `class_work.manage` and grants both to
super_admin, organization_manager and class_supervisor -- reach stays
bounded by each assignment's scope. Additive and idempotent; reversing
removes only these two permissions.
"""

from django.db import migrations

PERMISSIONS = (
    ("class_work.view", "Class work", "View announcements, calendar, assignments and class quizzes"),
    ("class_work.manage", "Class work", "Manage announcements, calendar, assignments and class quizzes"),
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
        ("admin_dashboard", "0005_class_library_permissions"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
