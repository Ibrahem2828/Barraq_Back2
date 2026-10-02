"""Permissions for ساحة المعرفة / صفي's lesson-linked Q&A.

Creates `knowledge.view` and `knowledge.moderate` and grants both to
super_admin, organization_manager and class_supervisor. This is
moderation-only reach over student-authored content, unlike
class_work.manage (which a teacher uses to author announcements,
assignments and quizzes day to day). Additive and idempotent; reversing
removes only these two permissions.
"""

from django.db import migrations

PERMISSIONS = (
    ("knowledge.view", "Knowledge square", "View classroom knowledge threads and replies"),
    ("knowledge.moderate", "Knowledge square", "Moderate (delete) classroom knowledge threads and replies"),
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
        ("admin_dashboard", "0007_journey_permissions"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
