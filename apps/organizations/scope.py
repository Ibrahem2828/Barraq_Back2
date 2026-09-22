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

from .models import AdminRoleScope, Classroom, Organization


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


class _Unrestricted:
    """Platform-wide reach: every row, with no `IN (...)` over the table.

    A distinct object rather than `None`, because `None` is what Python
    returns from any code path that falls off the end of a function. With
    `None` meaning "everything", a forgotten `return` inside a scope
    resolver silently granted the whole platform -- the one failure mode
    this module cannot afford. An unknown value now means *no* access.
    """

    __slots__ = ()

    def __repr__(self):  # pragma: no cover - diagnostics only
        return "UNRESTRICTED"


#: Sentinel for "every tenant". Compare with `is`, never truthiness.
UNRESTRICTED = _Unrestricted()

#: Nothing at all. Spelled out so a reader never has to ask whether an empty
#: result means "no rows matched" or "not allowed" -- here it always means
#: the second.
NO_ACCESS: frozenset = frozenset()


def is_unrestricted(ids):
    """Whether a scope result means platform-wide reach."""
    return ids is UNRESTRICTED


def _normalize(ids):
    """Coerce a scope result, treating anything unrecognised as no access.

    `None` reaches here only from a bug -- a resolver that returned nothing
    -- and is deliberately read as an empty set. Failing closed on a
    programming error is the entire point of the sentinel.
    """
    if ids is UNRESTRICTED:
        return UNRESTRICTED
    return set(ids) if ids else set()


def _active_scope_rows(user):
    return AdminRoleScope.objects.filter(
        admin_user_role__user=user,
        admin_user_role__is_active=True,
        admin_user_role__role__is_active=True,
    ).select_related("organization", "classroom")


def _granting_scope_rows(user, permission=None):
    """Scope rows belonging to grants that actually supply `permission`.

    This is the difference between "the account holds users.view somewhere"
    and "the account holds users.view *here*". Resolving those separately --
    every permission the account has, crossed with every scope it has -- lets
    two harmless grants combine into one nobody issued:

        Role 1: users.view       on Organization A
        Role 2: classes.update   on Organization B

    the union says users.view and the union says {A, B}, so the account reads
    Organization B's learners on the strength of a grant that only ever
    allowed renaming its classes. Each row is therefore filtered by the
    permission its own role carries.
    """
    rows = _active_scope_rows(user)
    if permission:
        rows = rows.filter(
            admin_user_role__role__permissions__code=permission,
            admin_user_role__role__is_active=True,
        )
    return rows


def has_global_scope(user, permission=None):
    """Platform-wide reach, optionally for one permission.

    `is_superuser` and the super_admin role keep the unrestricted access they
    had before organizations existed -- Phase 3 must not quietly demote the
    platform owner.

    With a permission, the question is narrower and the only safe one to ask
    of a multi-role account: is this permission granted globally? A support
    role scoped globally must not make an organization-scoped
    `organizations.archive` global too.
    """
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if is_super_admin_user(user):
        return True
    return _granting_scope_rows(user, permission).filter(scope_type=AdminRoleScope.ScopeType.GLOBAL).exists()


def accessible_organization_ids(user, permission=None):
    """Organization ids this account may act on, or UNRESTRICTED for all."""
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return NO_ACCESS
    if permission is not None and not user_has_scoped_permission(user, permission):
        return NO_ACCESS
    if has_global_scope(user, permission):
        return UNRESTRICTED

    rows = _granting_scope_rows(user, permission).exclude(scope_type=AdminRoleScope.ScopeType.GLOBAL)
    # A class scope is not organization scope.  Folding the class's parent
    # into this set made a supervisor for 10-A an organization administrator:
    # membership listings, organization invitations and aggregate counts then
    # leaked the whole school.  Parent metadata is deliberately exposed by the
    # classroom serializer instead; organization operations require an actual
    # organization grant.
    return {
        row.organization_id
        for row in rows
        if row.scope_type == AdminRoleScope.ScopeType.ORGANIZATION and row.organization_id
    }


def accessible_classroom_ids(user, permission=None):
    """Classroom ids this account may act on, or UNRESTRICTED for all.

    An organization grant implies every class inside it. A class grant
    implies exactly one class and never widens to its siblings -- a
    supervisor for 10-A has no business in 10-B.
    """
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return NO_ACCESS
    if permission is not None and not user_has_scoped_permission(user, permission):
        return NO_ACCESS
    if has_global_scope(user, permission):
        return UNRESTRICTED

    rows = list(_granting_scope_rows(user, permission).exclude(scope_type=AdminRoleScope.ScopeType.GLOBAL))
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


def has_scope(user, permission=None, allowed_scope_types=None):
    """Whether an active grant supplies a permission and an allowed scope.

    A permission is necessary but cannot stand alone: assignments without a
    scope are intentionally ineffective.  ``allowed_scope_types`` lets an
    endpoint require an organization grant rather than treating a class grant
    over a child resource as permission to administer the parent.
    """
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if permission is not None and not user_has_scoped_permission(user, permission):
        return False
    if is_super_admin_user(user):
        return True
    rows = _granting_scope_rows(user, permission)
    if allowed_scope_types is not None:
        rows = rows.filter(scope_type__in=allowed_scope_types)
    return rows.exists()


def scope_organizations(user, queryset, permission=None):
    ids = _normalize(accessible_organization_ids(user, permission))
    if is_unrestricted(ids):
        return queryset
    return queryset.filter(id__in=ids)


def scope_classrooms(user, queryset, permission=None):
    ids = _normalize(accessible_classroom_ids(user, permission))
    if is_unrestricted(ids):
        return queryset
    return queryset.filter(id__in=ids)


def scope_by_organization_field(user, queryset, permission=None, field="organization_id"):
    """Narrow an organization-owned queryset to actual organization grants.

    Class scope is intentionally excluded.  A row with an organization FK can
    describe the entire tenant (an organization invitation, for example), so
    using the class's parent id here would widen one class grant into a school
    grant.  Resources that are directly class-owned must combine this helper
    with ``scope_by_classroom_field`` explicitly.
    """
    ids = _normalize(accessible_organization_ids(user, permission))
    if is_unrestricted(ids):
        return queryset
    return queryset.filter(**{f"{field}__in": ids})


def scope_by_classroom_field(user, queryset, permission=None, field="classroom_id"):
    ids = _normalize(accessible_classroom_ids(user, permission))
    if is_unrestricted(ids):
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
    organization_ids = _normalize(accessible_organization_ids(user, permission))
    if is_unrestricted(organization_ids):
        return queryset
    classroom_ids = _normalize(accessible_classroom_ids(user, permission))
    if is_unrestricted(classroom_ids):
        return queryset
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
    ids = _normalize(accessible_organization_ids(user, permission))
    if is_unrestricted(ids):
        return organization
    organization_id = getattr(organization, "id", organization)
    if organization_id not in ids:
        raise ScopeDenied("organization_access_denied")
    return organization


def assert_classroom_allowed(user, classroom, permission=None):
    ids = _normalize(accessible_classroom_ids(user, permission))
    if is_unrestricted(ids):
        return classroom
    classroom_id = getattr(classroom, "id", classroom)
    if classroom_id not in ids:
        raise ScopeDenied("class_access_denied")
    return classroom


def describe_scope_rows(rows):
    """Format scope rows that are already in memory.

    Split out so a list endpoint can describe a page of accounts from one
    prefetch instead of re-querying per row, while `describe_scopes` keeps
    doing its own lookup for the single-account case.
    """
    described = []
    seen = set()
    for row in rows:
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


def viewer_scope_reach(viewer, permission=None):
    """The public ids a viewer may see, resolved once.

    Every row of the admin directory needs the same answer, so resolving it
    per row is the difference between one query and one per account on the
    page. Returns None when the viewer reaches everything.
    """
    if has_global_scope(viewer, permission):
        return None
    organization_ids = _normalize(accessible_organization_ids(viewer, permission))
    classroom_ids = _normalize(accessible_classroom_ids(viewer, permission))
    if is_unrestricted(organization_ids):  # pragma: no cover - defensive
        return None
    return {
        "organizations": {
            str(value)
            for value in Organization.objects.filter(id__in=organization_ids).values_list("public_id", flat=True)
        },
        "classrooms": {
            str(value)
            for value in Classroom.objects.filter(id__in=classroom_ids or []).values_list("public_id", flat=True)
        },
    }


def reduce_scopes_to_reach(described, reach):
    """Drop the entries a viewer with `reach` is not allowed to know about."""
    if reach is None:
        return described
    reduced = []
    for entry in described:
        # A global grant is never shown to a scoped viewer: it describes
        # reach over every tenant, including theirs.
        if entry.get("type") == AdminRoleScope.ScopeType.GLOBAL:
            continue
        classroom = entry.get("classroom") or {}
        organization = entry.get("organization") or {}
        if (
            classroom
            and classroom.get("public_id") in reach["classrooms"]
            or organization.get("public_id") in reach["organizations"]
        ):
            reduced.append(entry)
    return reduced


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
    organization_ids = _normalize(accessible_organization_ids(user, permission))
    if is_unrestricted(organization_ids):
        return UNRESTRICTED
    classroom_ids = _normalize(accessible_classroom_ids(user, permission))
    if is_unrestricted(classroom_ids):
        return UNRESTRICTED
    if not organization_ids and not classroom_ids:
        return NO_ACCESS

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
    ids = _normalize(scoped_user_ids(user, permission))
    if is_unrestricted(ids):
        return queryset
    return queryset.filter(**{f"{field}__in": ids})


def scope_admin_accounts(user, queryset, permission=None):
    """Other admin accounts this account may see.

    A scoped manager administers people inside their own tenant, so the
    overlap is on scope rather than on membership: admins holding a grant
    over an organization or class this caller also reaches, plus the caller
    themselves -- never the platform's full staff directory.
    """
    organization_ids = _normalize(accessible_organization_ids(user, permission))
    if is_unrestricted(organization_ids):
        return queryset
    classroom_ids = _normalize(accessible_classroom_ids(user, permission))
    if is_unrestricted(classroom_ids):
        return queryset
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

    # Permission classes read this marker before a queryset is evaluated, so
    # an active assignment with no scope is rejected with 403 rather than
    # being mistaken for a successful empty list.  Platform catalogues inherit
    # the same fail-closed rule; their views may narrow this to GLOBAL where a
    # mutation affects every tenant.
    requires_scope = True

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


def assert_global_scope(user, permission=None):
    """Guard for platform-wide aggregates.

    A scoped manager must never receive a platform total: a count leaks the
    shape of every other tenant as surely as a list leaks their rows. Scoped
    accounts read their own numbers from the organization overview instead.
    """
    if not has_global_scope(user, permission):
        raise ScopeDenied("global_scope_required")


def resolve_grantable_scopes(actor, raw_scopes, *, permission="admins.assign_roles"):
    """Turn requested scopes into model objects, refusing escalation.

    An operator can only grant reach they already hold. Without this check,
    `admins.assign_roles` is a privilege-escalation primitive: a manager of
    one school assigns a colleague -- or themselves -- a role scoped to
    another school, or to the whole platform, and every boundary in this
    module is gone. The permission to assign roles is not the permission to
    invent reach.

    Scope omission is refused even to platform administrators.  GLOBAL is an
    explicit high-trust grant, not a default hidden behind an absent request
    field.
    """
    from rest_framework import serializers

    from .models import Classroom, Organization

    if not user_has_scoped_permission(actor, permission):
        raise serializers.ValidationError({"scopes": "You cannot grant administrative scope."})
    if not raw_scopes:
        raise serializers.ValidationError({"scopes": "At least one explicit scope is required."})

    actor_is_global = has_global_scope(actor, permission)
    allowed_organizations = _normalize(accessible_organization_ids(actor, permission))
    allowed_classrooms = _normalize(accessible_classroom_ids(actor, permission))

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
                not is_unrestricted(allowed_organizations) and organization.id not in allowed_organizations
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
            }
        )
    return resolved


def validate_grantable_roles(actor, roles, scopes):
    """Ensure every delegated permission is held at every delegated scope.

    Checking only the actor's union of permission strings and union of scopes
    lets unrelated assignments combine into a grant nobody issued.  A caller
    with ``admins.assign_roles`` in organization A and ``support.view`` in a
    separate global support assignment must not be able to delegate global
    support access.  Each role permission is therefore checked against each
    requested target independently.
    """
    from rest_framework import serializers

    if not scopes:
        raise serializers.ValidationError({"scopes": "At least one explicit scope is required."})

    for role in roles:
        permission_codes = list(role.permissions.filter(is_active=True).values_list("code", flat=True))
        if role.code == "super_admin" and scopes != [{"scope_type": AdminRoleScope.ScopeType.GLOBAL}]:
            raise serializers.ValidationError(
                {"scopes": "The super_admin role requires exactly one GLOBAL scope."}
            )
        for scope in scopes:
            scope_type = scope["scope_type"]
            for code in permission_codes:
                if scope_type == AdminRoleScope.ScopeType.GLOBAL:
                    allowed = has_global_scope(actor, code)
                elif scope_type == AdminRoleScope.ScopeType.ORGANIZATION:
                    organization = scope["organization"]
                    ids = _normalize(accessible_organization_ids(actor, code))
                    allowed = is_unrestricted(ids) or organization.id in ids
                else:
                    classroom = scope["classroom"]
                    ids = _normalize(accessible_classroom_ids(actor, code))
                    allowed = is_unrestricted(ids) or classroom.id in ids
                if not allowed:
                    raise serializers.ValidationError(
                        {"roles": "Cannot grant a role beyond your active permission scope."}
                    )


def revoke_grants_within_scope(target, actor, *, permission="admins.assign_roles"):
    """Withdraw `target`'s grants, but only the ones `actor` can reach.

    An operator scoped to one organization must be able to remove a
    supervisor from it, and must not be able to strip that person of a role
    in someone else's. So this removes the scope rows inside the actor's
    reach and retires an assignment only once nothing is left of it --
    revocation is bounded by exactly the same boundary as everything else.

    A platform operator reaches everything and therefore removes everything,
    which is the behaviour that existed before scope.

    Returns the number of scope rows withdrawn.
    """
    from apps.admin_dashboard.models import AdminUserRole

    assignments = AdminUserRole.objects.filter(user=target, is_active=True)
    rows = AdminRoleScope.objects.filter(admin_user_role__in=assignments)

    if not has_global_scope(actor, permission):
        organization_ids = _normalize(accessible_organization_ids(actor, permission))
        classroom_ids = _normalize(accessible_classroom_ids(actor, permission))
        if is_unrestricted(organization_ids):  # pragma: no cover - defensive
            raise ScopeDenied("scope_access_denied")
        rows = rows.filter(Q(organization_id__in=organization_ids) | Q(classroom_id__in=classroom_ids or []))

    removed = rows.count()
    rows.delete()

    # An assignment with no scope left grants nothing, so it is retired
    # rather than kept as a row that silently means "no access".
    for assignment in assignments:
        if not AdminRoleScope.objects.filter(admin_user_role=assignment).exists():
            assignment.is_active = False
            assignment.save(update_fields=["is_active"])
    return removed


def describe_scopes_for_viewer(target, viewer, permission=None):
    """`target`'s scopes, reduced to the ones `viewer` is allowed to know.

    The admin directory has to say what each account administers, or it
    cannot answer "who supervises this class". But an account may work in
    two organizations, and showing all of its grants to a manager of one of
    them names the other -- the directory becomes a way to enumerate tenants
    through the people who staff them.

    A platform operator sees everything, because they already can.
    """
    return reduce_scopes_to_reach(describe_scopes(target), viewer_scope_reach(viewer, permission))


__all__ = [
    "viewer_scope_reach",
    "reduce_scopes_to_reach",
    "describe_scope_rows",
    "describe_scopes_for_viewer",
    "revoke_grants_within_scope",
    "is_unrestricted",
    "NO_ACCESS",
    "UNRESTRICTED",
    "resolve_grantable_scopes",
    "validate_grantable_roles",
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
