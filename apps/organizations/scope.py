"""The single place that answers "on whose data?".

Phase 1 answered *what* an account may do. This answers *whose* data it may
do it to, and the two stay separate: a permission never names an
organization, and a scope never names an action. Encoding an id into a
permission string -- `school_47.users.view` -- would make every grant a
migration and every revocation a search-and-replace.

Every scoped endpoint routes through here. Scattering `.filter(organization_id=...)`
across views is how one forgotten `retrieve()` becomes a cross-tenant read:
the list looks correct, so nobody looks at the detail route.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from rest_framework.exceptions import NotFound

from apps.admin_dashboard.services import get_user_admin_permissions, is_super_admin_user

from .models import AdminRoleScope, Classroom


class ScopeDenied(NotFound):
    """Raised when an object exists but lies outside the caller's scope.

    A 404 rather than a 403, and deliberately: telling a manager that
    organization 12 exists but is not theirs is itself the disclosure that
    scoping exists to prevent. Subclassing DRF's NotFound means every caller
    gets this -- including views outside this app -- instead of each viewset
    remembering to translate a bare exception into the right status.
    """

    default_detail = "غير موجود."

    def __init__(self, code="scope_access_denied", detail=None):
        self.domain_code = code
        super().__init__(detail)


def _active_scope_rows(user):
    return AdminRoleScope.objects.filter(
        admin_user_role__user=user,
        admin_user_role__is_active=True,
        admin_user_role__role__is_active=True,
    ).select_related("organization", "classroom")


def has_global_scope(user):
    """Platform-wide reach.

    `is_superuser` and the super_admin role keep the unrestricted access they
    had before organizations existed -- Phase 3 must not quietly demote the
    platform owner.
    """
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if is_super_admin_user(user):
        return True
    return _active_scope_rows(user).filter(scope_type=AdminRoleScope.ScopeType.GLOBAL).exists()


def accessible_organization_ids(user, permission=None):
    """Organization ids this account may act on, or None meaning "all".

    None rather than a list of every id: a global admin must not be turned
    into an `IN (...)` over the whole table, and the difference between
    "everything" and "these 4000" matters for both correctness and the query
    plan.
    """
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return set()
    if permission is not None and not user_has_scoped_permission(user, permission):
        return set()
    if has_global_scope(user):
        return None

    rows = _active_scope_rows(user).exclude(scope_type=AdminRoleScope.ScopeType.GLOBAL)
    organization_ids = set()
    for row in rows:
        if row.scope_type == AdminRoleScope.ScopeType.ORGANIZATION and row.organization_id:
            organization_ids.add(row.organization_id)
        elif row.scope_type == AdminRoleScope.ScopeType.CLASS and row.classroom_id:
            # A class grant does not carry the organization with it. It is
            # recorded here only so an organization-level listing can show the
            # one class the supervisor reaches -- `accessible_classroom_ids`
            # is what actually limits them.
            organization_ids.add(row.classroom.organization_id)
    return organization_ids


def accessible_classroom_ids(user, permission=None):
    """Classroom ids this account may act on, or None meaning "all".

    An organization grant implies every class inside it. A class grant
    implies exactly one class and never widens to its siblings -- a
    supervisor for 10-A has no business in 10-B.
    """
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return set()
    if permission is not None and not user_has_scoped_permission(user, permission):
        return set()
    if has_global_scope(user):
        return None

    rows = list(_active_scope_rows(user).exclude(scope_type=AdminRoleScope.ScopeType.GLOBAL))
    organization_ids = {
        row.organization_id
        for row in rows
        if row.scope_type == AdminRoleScope.ScopeType.ORGANIZATION and row.organization_id
    }
    classroom_ids = {
        row.classroom_id for row in rows if row.scope_type == AdminRoleScope.ScopeType.CLASS and row.classroom_id
    }
    if organization_ids:
        classroom_ids |= set(
            Classroom.objects.filter(organization_id__in=organization_ids).values_list("id", flat=True)
        )
    return classroom_ids


def user_has_scoped_permission(user, permission):
    """Whether the account holds the permission at all.

    Scope narrows a permission; it never grants one. An account with an
    organization scope but without `classes.view` sees no classes anywhere,
    which is why permission is checked before scope rather than instead of it.
    """
    if not permission:
        return True
    return permission in get_user_admin_permissions(user)


def scope_organizations(user, queryset, permission=None):
    ids = accessible_organization_ids(user, permission)
    if ids is None:
        return queryset
    return queryset.filter(id__in=ids)


def scope_classrooms(user, queryset, permission=None):
    ids = accessible_classroom_ids(user, permission)
    if ids is None:
        return queryset
    return queryset.filter(id__in=ids)


def scope_by_organization_field(user, queryset, permission=None, field="organization_id"):
    """Narrow any queryset that carries an organization foreign key."""
    ids = accessible_organization_ids(user, permission)
    if ids is None:
        return queryset
    return queryset.filter(**{f"{field}__in": ids})


def scope_by_classroom_field(user, queryset, permission=None, field="classroom_id"):
    ids = accessible_classroom_ids(user, permission)
    if ids is None:
        return queryset
    return queryset.filter(**{f"{field}__in": ids})


def scope_users(user, queryset, permission=None):
    """Users a scoped account may see.

    Visibility follows membership: an account reaches a learner because that
    learner belongs to an organization or class it was granted, never because
    it knows their id. A scoped manager with no grants sees nobody rather
    than everybody -- `none()` is the safe answer to an empty scope, and the
    one a bug is least likely to turn into a leak.
    """
    organization_ids = accessible_organization_ids(user, permission)
    if organization_ids is None:
        return queryset
    classroom_ids = accessible_classroom_ids(user, permission)
    if not organization_ids and not classroom_ids:
        return queryset.none()
    return queryset.filter(
        Q(
            organization_memberships__organization_id__in=organization_ids,
            organization_memberships__status="active",
        )
        | Q(
            class_memberships__classroom_id__in=classroom_ids or [],
            class_memberships__status="active",
        )
    ).distinct()


def assert_organization_allowed(user, organization, permission=None):
    """Object-level gate for a single organization.

    List filtering alone is not isolation: the attack is a direct id against
    a detail route whose queryset was never scoped. Call this on every
    retrieve, update, archive and bulk element.
    """
    ids = accessible_organization_ids(user, permission)
    if ids is None:
        return organization
    organization_id = getattr(organization, "id", organization)
    if organization_id not in ids:
        raise ScopeDenied("organization_access_denied")
    return organization


def assert_classroom_allowed(user, classroom, permission=None):
    ids = accessible_classroom_ids(user, permission)
    if ids is None:
        return classroom
    classroom_id = getattr(classroom, "id", classroom)
    if classroom_id not in ids:
        raise ScopeDenied("class_access_denied")
    return classroom


def describe_scopes(user):
    """Compact scope summary for the identity response.

    Only what a client needs to render context: what kind of scope, and the
    name of the thing it applies to. No member counts, no configuration, no
    ids beyond the public ones -- an identity payload is not a place to leak
    an organization's shape.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []
    if is_super_admin_user(user):
        return [{"type": AdminRoleScope.ScopeType.GLOBAL}]

    described = []
    seen = set()
    for row in _active_scope_rows(user):
        key = (row.scope_type, row.organization_id, row.classroom_id)
        if key in seen:
            continue
        seen.add(key)
        entry = {"type": row.scope_type}
        if row.scope_type == AdminRoleScope.ScopeType.ORGANIZATION and row.organization:
            entry["organization"] = {
                "public_id": str(row.organization.public_id),
                "name": row.organization.name,
                "organization_type": row.organization.organization_type,
            }
        elif row.scope_type == AdminRoleScope.ScopeType.CLASS and row.classroom:
            entry["classroom"] = {
                "public_id": str(row.classroom.public_id),
                "name": row.classroom.name,
            }
            entry["organization"] = {
                "public_id": str(row.classroom.organization.public_id),
                "name": row.classroom.organization.name,
                "organization_type": row.classroom.organization.organization_type,
            }
        described.append(entry)
    return described


def organization_for_write(user, organization, permission=None):
    """Resolve the organization a write applies to.

    Always the server's own lookup intersected with scope, never the id in
    the request body. "Create a class in organization 12" from a manager of
    organization 7 is refused here rather than being taken at face value --
    which is the single most common way a tenant boundary is crossed.
    """
    if organization is None:
        raise ScopeDenied("organization_not_found")
    return assert_organization_allowed(user, organization, permission)


def scoped_user_ids(user, permission=None):
    """Ids of the users this account may see, or None meaning "all".

    Visibility follows membership. An account reaches a learner because
    that learner belongs to an organization or class it was granted, never
    because it knows their id.
    """
    organization_ids = accessible_organization_ids(user, permission)
    if organization_ids is None:
        return None
    classroom_ids = accessible_classroom_ids(user, permission)
    if not organization_ids and not classroom_ids:
        return set()

    from django.contrib.auth import get_user_model

    return set(
        get_user_model()
        .objects.filter(
            Q(
                organization_memberships__organization_id__in=organization_ids,
                organization_memberships__status="active",
            )
            | Q(
                class_memberships__classroom_id__in=classroom_ids or [],
                class_memberships__status="active",
            )
        )
        .values_list("id", flat=True)
    )


def scope_by_user_field(user, queryset, permission=None, field="user_id"):
    """Narrow any queryset whose rows belong to a learner.

    Sources, plans, quizzes, attempts, interactions, tickets, subscriptions
    and audit entries are all tenant data by virtue of whose they are. They
    carry no organization column, so the tenant boundary reaches them
    through their owner.
    """
    ids = scoped_user_ids(user, permission)
    if ids is None:
        return queryset
    return queryset.filter(**{f"{field}__in": ids})


def scope_admin_accounts(user, queryset, permission=None):
    """Other admin accounts this account may see.

    A scoped manager administers people inside their own tenant, so the
    overlap is on scope rather than on membership: admins holding a grant
    over an organization or class this caller also reaches, plus the caller
    themselves -- never the platform's full staff directory.
    """
    organization_ids = accessible_organization_ids(user, permission)
    if organization_ids is None:
        return queryset
    classroom_ids = accessible_classroom_ids(user, permission)
    if not organization_ids and not classroom_ids:
        return queryset.filter(pk=user.pk)
    return queryset.filter(
        Q(admin_user_roles__scopes__organization_id__in=organization_ids)
        | Q(admin_user_roles__scopes__classroom_id__in=classroom_ids or [])
        | Q(pk=user.pk)
    ).distinct()


class TenantScopedQuerysetMixin:
    """Applies the tenant boundary to a DRF viewset at one point.

    `filter_queryset` rather than `get_queryset`, because DRF routes both
    list and `get_object` through it -- scoping only the list is how a
    detail route keeps answering for ids it should never have seen.

    `tenant_user_field` is mandatory. A new admin viewset that forgets it
    fails loudly at request time instead of quietly serving every tenant,
    which is the failure mode this whole module exists to prevent.
    """

    _UNSET = "__tenant_user_field_not_declared__"

    #: For viewsets that apply the boundary themselves, by organization or
    #: class, rather than through a row's owner -- the organization
    #: endpoints. Distinct from None so "scoped elsewhere" can never be read
    #: as "not tenant data".
    SCOPED_BY_ORGANIZATION = "__scoped_by_organization__"

    #: Column linking a row to its owning learner, "admins" for the staff
    #: directory, SCOPED_BY_ORGANIZATION, or None for a genuine platform
    #: catalogue (roles, permissions, plans, curriculum).
    tenant_user_field: str | None = _UNSET

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        field = getattr(self, "tenant_user_field", self._UNSET)
        if field == self._UNSET:
            raise ImproperlyConfigured(
                f"{type(self).__name__} must declare tenant_user_field "
                "(a column name, 'admins', or None for platform catalogues)."
            )
        if field is None or field == self.SCOPED_BY_ORGANIZATION:
            return queryset
        permission = self.get_required_permission() if hasattr(self, "get_required_permission") else None
        if field == "admins":
            return scope_admin_accounts(self.request.user, queryset, permission)
        return scope_by_user_field(self.request.user, queryset, permission, field)


def assert_global_scope(user):
    """Guard for platform-wide aggregates.

    A scoped manager must never receive a platform total: a count leaks the
    shape of every other tenant as surely as a list leaks their rows. Scoped
    accounts read their own numbers from the organization overview instead.
    """
    if not has_global_scope(user):
        raise ScopeDenied("global_scope_required")


def resolve_grantable_scopes(actor, raw_scopes):
    """Turn requested scopes into model objects, refusing escalation.

    An operator can only grant reach they already hold. Without this check,
    `admins.assign_roles` is a privilege-escalation primitive: a manager of
    one school assigns a colleague -- or themselves -- a role scoped to
    another school, or to the whole platform, and every boundary in this
    module is gone. The permission to assign roles is not the permission to
    invent reach.

    Returns None for "global", which is what `assign_roles_to_user` already
    means by an omitted scope.
    """
    from rest_framework import serializers

    from .models import Classroom, Organization

    if not raw_scopes:
        if not has_global_scope(actor):
            raise serializers.ValidationError({"scopes": "A scoped operator must state the scope being granted."})
        return None

    actor_is_global = has_global_scope(actor)
    allowed_organizations = accessible_organization_ids(actor)
    allowed_classrooms = accessible_classroom_ids(actor)

    resolved = []
    for entry in raw_scopes:
        scope_type = entry["scope_type"]

        if scope_type == AdminRoleScope.ScopeType.GLOBAL:
            if not actor_is_global:
                raise serializers.ValidationError(
                    {"scopes": "Only a platform administrator can grant platform-wide scope."}
                )
            resolved.append({"scope_type": scope_type})
            continue

        if scope_type == AdminRoleScope.ScopeType.ORGANIZATION:
            organization = Organization.objects.filter(public_id=entry.get("organization")).first()
            if organization is None or (
                allowed_organizations is not None and organization.id not in allowed_organizations
            ):
                # One wording for both "does not exist" and "not yours": a
                # distinct message would confirm an organization the actor
                # is not allowed to know about.
                raise serializers.ValidationError({"scopes": "Unknown organization."})
            resolved.append({"scope_type": scope_type, "organization": organization})
            continue

        classroom = Classroom.objects.filter(public_id=entry.get("classroom")).first()
        if classroom is None or (allowed_classrooms is not None and classroom.id not in allowed_classrooms):
            raise serializers.ValidationError({"scopes": "Unknown class."})
        resolved.append(
            {
                "scope_type": scope_type,
                "classroom": classroom,
                "organization": classroom.organization,
            }
        )
    return resolved


__all__ = [
    "resolve_grantable_scopes",
    "scoped_user_ids",
    "scope_by_user_field",
    "scope_admin_accounts",
    "assert_global_scope",
    "TenantScopedQuerysetMixin",
    "ScopeDenied",
    "accessible_classroom_ids",
    "accessible_organization_ids",
    "assert_classroom_allowed",
    "assert_organization_allowed",
    "describe_scopes",
    "has_global_scope",
    "organization_for_write",
    "scope_by_classroom_field",
    "scope_by_organization_field",
    "scope_classrooms",
    "scope_organizations",
    "scope_users",
    "user_has_scoped_permission",
]
