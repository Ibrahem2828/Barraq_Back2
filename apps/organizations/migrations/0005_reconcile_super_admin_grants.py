"""Give every super-admin account the grant that makes it one.

`User.role` is a classification; authority comes from an active super_admin
assignment with a GLOBAL scope (or is_superuser). Accounts classified as
super_admin without that grant existed in production and got 403 on every
admin endpoint. Each grant created here is recorded in AuditLog, which is
also how the reverse migration finds and removes exactly those grants.
"""

from django.db import migrations

ACTION = "admin.super_admin_grant_reconciled"
MIGRATION = "organizations.0005_reconcile_super_admin_grants"


def grant(apps, schema_editor):
    User = apps.get_model("users", "User")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")
    AdminUserRole = apps.get_model("admin_dashboard", "AdminUserRole")
    AdminRoleScope = apps.get_model("organizations", "AdminRoleScope")
    AuditLog = apps.get_model("admin_dashboard", "AuditLog")

    role = AdminRole.objects.filter(code="super_admin").first()
    if role is None:
        return
    for user in User.objects.filter(role="super_admin", is_active=True):
        assignment = AdminUserRole.objects.filter(user=user, role=role).first()
        if assignment is None:
            assignment = AdminUserRole.objects.create(user=user, role=role, is_active=True)
            created_assignment = True
        else:
            created_assignment = False
        has_global = AdminRoleScope.objects.filter(
            admin_user_role=assignment, scope_type="global"
        ).exists()
        if assignment.is_active and has_global:
            continue
        reactivated = not assignment.is_active
        if reactivated:
            assignment.is_active = True
            assignment.save(update_fields=["is_active"])
        scope = None
        if not has_global:
            # 0004's check constraint: a class-less, organization-less row.
            AdminRoleScope.objects.filter(admin_user_role=assignment).delete()
            scope = AdminRoleScope.objects.create(admin_user_role=assignment, scope_type="global")
        AuditLog.objects.create(
            actor=None,
            action=ACTION,
            target_type="user",
            target_id=str(user.pk),
            metadata={
                "migration": MIGRATION,
                "admin_user_role_id": assignment.pk,
                "created_assignment": created_assignment,
                "reactivated_assignment": reactivated,
                "scope_id": str(scope.pk) if scope else None,
            },
        )


def revoke(apps, schema_editor):
    AdminUserRole = apps.get_model("admin_dashboard", "AdminUserRole")
    AdminRoleScope = apps.get_model("organizations", "AdminRoleScope")
    AuditLog = apps.get_model("admin_dashboard", "AuditLog")
    for entry in AuditLog.objects.filter(action=ACTION, metadata__migration=MIGRATION):
        data = entry.metadata
        if data.get("created_assignment"):
            AdminUserRole.objects.filter(pk=data["admin_user_role_id"]).delete()
        else:
            if data.get("scope_id"):
                AdminRoleScope.objects.filter(pk=data["scope_id"]).delete()
            if data.get("reactivated_assignment"):
                AdminUserRole.objects.filter(pk=data["admin_user_role_id"]).update(is_active=False)
        entry.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0004_harden_admin_role_scope_integrity"),
        ("admin_dashboard", "0002_seed_default_rbac"),
        ("users", "0005_pendingregistration"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
