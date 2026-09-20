"""Take back the global scope 0002 granted to assignments that never had it.

0002 gave an explicit GLOBAL scope to *every* AdminUserRole row so that
"no scope rows" could safely start meaning "no access". That part was
necessary. What it got wrong was the population: it included deactivated
assignments and assignments whose role had been deactivated.

Those rows granted nothing before scope existed --
`get_user_admin_permissions` has only ever derived permissions from

    AdminUserRole.is_active = True   AND   AdminRole.is_active = True

-- so handing them platform-wide scope created authority that never was.
The damage is deferred rather than immediate: the assignment is inactive,
so it grants nothing today. It becomes real the moment someone re-enables
it to restore a colleague's old job, and what comes back is the entire
platform instead of the one school they should be given.

Why a forward migration rather than a corrected 0002
----------------------------------------------------
Rewriting 0002 only helps a database that has not run it yet. Any
environment that already applied it keeps the over-granted rows forever,
because Django will never re-run a migration it has recorded. A forward
correction fixes both cases and needs no proof about where the original
was applied -- and proving that negative about every environment a
repository has ever touched is not something a migration should depend on.

What is deleted, and why that set is exactly right
--------------------------------------------------
Only global scope rows whose assignment is inactive, or whose role is
inactive. Such a row cannot have come from an operator: every deliberate
grant goes through `assign_roles_to_user`, which activates the assignment
it writes. So this set is precisely 0002's over-grant.

The one other way to reach that state -- an operator granting global scope
and later deactivating the assignment -- lands on the same answer. The
assignment already grants nothing, and re-enabling it must go through the
assignment API, which now refuses to guess a scope. Removing the row is
what the corrected backfill would have produced.

Deliberately untouched: active assignments on active roles, which keep the
reach they had; and `is_superuser`, which is authority in itself and does
not depend on a row at all.

Idempotent: deleting rows that are already gone is a no-op, so re-running
changes nothing.
"""

from django.db import migrations
from django.db.models import Q


def revoke_overgranted_global_scope(apps, schema_editor):
    AdminRoleScope = apps.get_model("organizations", "AdminRoleScope")

    AdminRoleScope.objects.filter(
        Q(admin_user_role__is_active=False) | Q(admin_user_role__role__is_active=False),
        scope_type="global",
    ).delete()


def restore_overgranted_global_scope(apps, schema_editor):
    """Deliberately a no-op.

    The reverse of "remove authority that should never have existed" is to
    grant it back, which is not something a rollback should do quietly. The
    rows are recoverable by re-running 0002's rule if anyone genuinely wants
    them, and that would be a deliberate act rather than a side effect of
    stepping a migration backwards.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0002_backfill_global_admin_scope"),
    ]

    operations = [
        migrations.RunPython(revoke_overgranted_global_scope, restore_overgranted_global_scope),
    ]
