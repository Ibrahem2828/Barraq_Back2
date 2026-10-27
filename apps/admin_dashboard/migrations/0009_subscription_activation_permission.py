"""Permission for activating a paid subscription after a verified payment.

Creates `subscriptions.activate` and grants it to super_admin and finance only.
It is deliberately separate from `subscriptions.update`: updating a record is
routine support work, while activating hands out paid access and therefore
needs a payment reference and leaves an audit trail. Additive and idempotent;
reversing removes only this permission.
"""

from django.db import migrations

CODE = "subscriptions.activate"
NAME = "Activate a paid subscription after verifying an external payment"
ROLES = ("super_admin", "finance")


def grant(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")
    permission, _ = AdminPermission.objects.get_or_create(
        code=CODE, defaults={"name": NAME, "category": "Subscriptions", "is_active": True}
    )
    for role in AdminRole.objects.filter(code__in=ROLES):
        role.permissions.add(permission)


def revoke(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminPermission.objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("admin_dashboard", "0008_knowledge_permissions"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
