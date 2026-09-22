from django.db import migrations, models
from django.db.models import Q


def normalize_admin_role_scopes(apps, schema_editor):
    """Canonicalize rows before adding constraints that make ambiguity fatal."""

    AdminRoleScope = apps.get_model("organizations", "AdminRoleScope")

    # A class already identifies its organization.  Retaining a second parent
    # field made contradictory rows possible and was never consulted by scope
    # resolution, so normalize it away before enforcing the canonical shape.
    AdminRoleScope.objects.filter(scope_type="class").update(organization=None)

    for scope_type, fields in (
        ("global", ("admin_user_role_id",)),
        ("organization", ("admin_user_role_id", "organization_id")),
        ("class", ("admin_user_role_id", "classroom_id")),
    ):
        duplicates = (
            AdminRoleScope.objects.filter(scope_type=scope_type)
            .values(*fields)
            .annotate(count=models.Count("id"))
            .filter(count__gt=1)
        )
        for duplicate in duplicates.iterator():
            filters = {field: duplicate[field] for field in fields}
            ids = list(
                AdminRoleScope.objects.filter(scope_type=scope_type, **filters)
                .order_by("id")
                .values_list("id", flat=True)
            )
            AdminRoleScope.objects.filter(id__in=ids[1:]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0003_revoke_overgranted_global_scope"),
    ]

    operations = [
        migrations.RunPython(normalize_admin_role_scopes, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="adminrolescope",
            name="admin_role_scope_shape_is_valid",
        ),
        migrations.AddConstraint(
            model_name="adminrolescope",
            constraint=models.CheckConstraint(
                condition=(
                    Q(scope_type="global", organization__isnull=True, classroom__isnull=True)
                    | Q(scope_type="organization", organization__isnull=False, classroom__isnull=True)
                    | Q(scope_type="class", organization__isnull=True, classroom__isnull=False)
                ),
                name="admin_role_scope_shape_is_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="adminrolescope",
            constraint=models.UniqueConstraint(
                fields=("admin_user_role",),
                condition=Q(scope_type="global"),
                name="unique_admin_role_global_scope",
            ),
        ),
        migrations.AddConstraint(
            model_name="adminrolescope",
            constraint=models.UniqueConstraint(
                fields=("admin_user_role", "organization"),
                condition=Q(scope_type="organization"),
                name="unique_admin_role_organization_scope",
            ),
        ),
        migrations.AddConstraint(
            model_name="adminrolescope",
            constraint=models.UniqueConstraint(
                fields=("admin_user_role", "classroom"),
                condition=Q(scope_type="class"),
                name="unique_admin_role_class_scope",
            ),
        ),
    ]
