"""Read-only integrity summary for administrative RBAC."""

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from apps.admin_dashboard.models import AdminRole, AdminUserRole
from apps.organizations.models import AdminRoleScope, Classroom, Organization


class Command(BaseCommand):
    help = "Report aggregate administrative RBAC integrity counts without personal data."

    def handle(self, *args, **options):
        assignments = AdminUserRole.objects.all()
        scopes = AdminRoleScope.objects.all()

        duplicate_global = sum(
            row["count"] - 1
            for row in scopes.filter(scope_type=AdminRoleScope.ScopeType.GLOBAL)
            .values("admin_user_role_id")
            .annotate(count=Count("id"))
            .filter(count__gt=1)
        )
        duplicate_organization = sum(
            row["count"] - 1
            for row in scopes.filter(scope_type=AdminRoleScope.ScopeType.ORGANIZATION)
            .values("admin_user_role_id", "organization_id")
            .annotate(count=Count("id"))
            .filter(count__gt=1)
        )
        duplicate_classroom = sum(
            row["count"] - 1
            for row in scopes.filter(scope_type=AdminRoleScope.ScopeType.CLASS)
            .values("admin_user_role_id", "classroom_id")
            .annotate(count=Count("id"))
            .filter(count__gt=1)
        )

        invalid_shape = scopes.exclude(
            Q(scope_type=AdminRoleScope.ScopeType.GLOBAL, organization__isnull=True, classroom__isnull=True)
            | Q(
                scope_type=AdminRoleScope.ScopeType.ORGANIZATION,
                organization__isnull=False,
                classroom__isnull=True,
            )
            | Q(scope_type=AdminRoleScope.ScopeType.CLASS, organization__isnull=True, classroom__isnull=False)
        ).count()

        report = {
            "active_assignments": assignments.filter(
                is_active=True,
                role__is_active=True,
                user__is_active=True,
            ).count(),
            "inactive_or_invalid_assignments": assignments.filter(
                Q(is_active=False) | Q(role__is_active=False) | Q(user__is_active=False)
            ).count(),
            "active_roles": AdminRole.objects.filter(is_active=True).count(),
            "inactive_roles": AdminRole.objects.filter(is_active=False).count(),
            "global_scopes": scopes.filter(scope_type=AdminRoleScope.ScopeType.GLOBAL).count(),
            "organization_scopes": scopes.filter(scope_type=AdminRoleScope.ScopeType.ORGANIZATION).count(),
            "class_scopes": scopes.filter(scope_type=AdminRoleScope.ScopeType.CLASS).count(),
            "assignments_without_scopes": assignments.filter(scopes__isnull=True).count(),
            "assignments_pointing_to_inactive_roles": assignments.filter(role__is_active=False).count(),
            # Foreign keys make a true missing target impossible through the
            # ORM/database.  These counts make that invariant observable in a
            # production-safe report rather than printing target identities.
            "orphaned_organization_scopes": scopes.filter(
                scope_type=AdminRoleScope.ScopeType.ORGANIZATION, organization__isnull=True
            ).count(),
            "orphaned_class_scopes": scopes.filter(
                scope_type=AdminRoleScope.ScopeType.CLASS, classroom__isnull=True
            ).count(),
            "scopes_to_inactive_organizations": scopes.filter(
                organization__status__in=[Organization.Status.INACTIVE, Organization.Status.ARCHIVED]
            ).count(),
            "scopes_to_archived_classes": scopes.filter(classroom__status=Classroom.Status.ARCHIVED).count(),
            "invalid_scope_shapes": invalid_shape,
            "duplicate_global_scopes": duplicate_global,
            "duplicate_organization_scopes": duplicate_organization,
            "duplicate_class_scopes": duplicate_classroom,
        }
        for key, value in report.items():
            self.stdout.write(f"{key.upper()} = {value}")
