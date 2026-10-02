"""Permissions for رحلة برّاق (gamified journey): platform-authored paths,
stations, unlockables and weekly challenges.

Creates `journey.view` and `journey.manage` and grants both to
super_admin and content_manager -- the same role that already authors
education stages, subjects and quiz moderation. A class's own shared goal
(ClassGoal) is not covered here: it reuses class_work.view/class_work.manage,
granted in migration 0006. Additive and idempotent; reversing removes only
these two permissions.
"""

from django.db import migrations

PERMISSIONS = (
    ("journey.view", "Journey", "View journey paths, unlockables and weekly challenges"),
    ("journey.manage", "Journey", "Manage journey paths, unlockables and weekly challenges"),
)
ROLES = ("super_admin", "content_manager")


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
        ("admin_dashboard", "0006_class_work_permissions"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
