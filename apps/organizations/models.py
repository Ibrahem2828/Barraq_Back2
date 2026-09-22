from __future__ import annotations

import secrets
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel

#: Alphabet for human-typed join codes.
#:
#: A character is excluded when it is ambiguous *with another character in
#: this same alphabet*: 0/O, 1/I/L, 5/S, 6/G and U/V. (8 and B would collide,
#: but B is absent, so 8 stays.) A code that is hard to transcribe off a
#: whiteboard gets retried, and retries against a secret are indistinguishable
#: from brute force -- so legibility here is a security property, not a
#: courtesy. 25**8 is ~1.5e11, which the join throttle makes unsearchable.
JOIN_CODE_AMBIGUOUS = "01ILO5SU6"
JOIN_CODE_ALPHABET = "ACDEFGHJKMNPQRTVWXY234789"
JOIN_CODE_LENGTH = 8
#: 27**8 ~= 2.8e11. With the join throttle applied this is not guessable, and
#: the token below (not the code) is what a link carries.
JOIN_TOKEN_BYTES = 32


def generate_join_code() -> str:
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))


def generate_join_token() -> str:
    return secrets.token_urlsafe(JOIN_TOKEN_BYTES)


class Organization(BaseModel):
    """A school or institute.

    One entity rather than separate School and Institute tables: they share
    every tenancy behaviour that matters -- membership, classes, invitations,
    scope -- and differ only in the label shown to a human. Splitting them
    would duplicate the entire isolation surface to express a display
    difference, and `organization_type` must never by itself decide security.
    """

    class Type(models.TextChoices):
        SCHOOL = "school", "School"
        INSTITUTE = "institute", "Institute"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        ARCHIVED = "archived", "Archived"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    name = models.CharField(max_length=255)
    organization_type = models.CharField(max_length=20, choices=Type.choices, default=Type.SCHOOL, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_organizations",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ("name",)
        indexes = [models.Index(fields=("status", "organization_type"), name="org_status_type_idx")]

    def __str__(self):
        return self.name

    @property
    def is_open(self):
        """Archived and inactive organizations keep their history but accept
        no new members."""
        return self.status == self.Status.ACTIVE


class OrganizationMembership(BaseModel):
    """The single authoritative answer to "does this user belong here".

    Deliberately a relation rather than a column on StudentProfile: a learner
    may move between institutions, an account may be staff in one and a
    student in another, and removal must leave a record. A column would
    represent only the present, and would quietly make history unrecoverable.
    """

    class MemberType(models.TextChoices):
        STUDENT = "student", "Student"
        STAFF = "staff", "Staff"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REMOVED = "removed", "Removed"
        LEFT = "left", "Left"
        ARCHIVED = "archived", "Archived"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organization_memberships"
    )
    member_type = models.CharField(max_length=20, choices=MemberType.choices, default=MemberType.STUDENT, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="approved_organization_memberships",
        null=True,
        blank=True,
    )
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            # One *active* membership per user per organization. Enforced in
            # the database rather than by a serializer check, because the
            # approval path is concurrent: two managers approving the same
            # request at the same moment both pass an application-level
            # "does it already exist" test.
            models.UniqueConstraint(
                fields=("organization", "user"),
                condition=Q(status="active"),
                name="unique_active_organization_membership",
            )
        ]
        indexes = [
            models.Index(fields=("organization", "status"), name="org_member_org_status_idx"),
            models.Index(fields=("user", "status"), name="org_member_user_status_idx"),
        ]

    def __str__(self):
        return f"{self.user_id}@{self.organization_id}"


class Classroom(BaseModel):
    """A class or section inside one organization."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="classrooms")
    name = models.CharField(max_length=255)
    # A human label such as "10-A". Not a secret and not a join code: see
    # Invitation, whose token/code are the only credentials that grant
    # anything. Keeping them separate stops a readable label from drifting
    # into an authentication factor.
    code = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_classrooms",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "code"),
                condition=~Q(code=""),
                name="unique_classroom_code_per_organization",
            )
        ]
        indexes = [models.Index(fields=("organization", "status"), name="classroom_org_status_idx")]

    def __str__(self):
        return f"{self.name} ({self.organization_id})"


class ClassMembership(BaseModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REMOVED = "removed", "Removed"
        LEFT = "left", "Left"
        ARCHIVED = "archived", "Archived"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="class_memberships")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="approved_class_memberships",
        null=True,
        blank=True,
    )
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("classroom", "user"),
                condition=Q(status="active"),
                name="unique_active_class_membership",
            )
        ]
        indexes = [
            models.Index(fields=("classroom", "status"), name="class_member_status_idx"),
            models.Index(fields=("user", "status"), name="class_member_user_idx"),
        ]

    def __str__(self):
        return f"{self.user_id}@class:{self.classroom_id}"


class Invitation(BaseModel):
    """A credential that lets a learner ask to join a class.

    Carries two independent secrets: `token` for a link and `code` for
    something a teacher can read aloud. Both are generated with `secrets`;
    neither is derived from a database id, because an enumerable identifier
    used as a credential is not a credential.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invitations")
    classroom = models.ForeignKey(
        Classroom, on_delete=models.CASCADE, related_name="invitations", null=True, blank=True
    )
    token = models.CharField(max_length=128, unique=True, default=generate_join_token, editable=False)
    code = models.CharField(max_length=40, unique=True, default=generate_join_code, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    # 0 means unlimited. Incremented under a row lock at redemption time --
    # see services.redeem_invitation -- because read-then-increment without a
    # lock lets two simultaneous redemptions both pass the limit check.
    max_uses = models.PositiveIntegerField(default=0)
    usage_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_invitations",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("organization", "status"), name="invitation_org_status_idx"),
            models.Index(fields=("classroom", "status"), name="invitation_class_status_idx"),
        ]

    def __str__(self):
        return f"invitation:{self.pk}"

    def is_usable(self, *, now):
        """Every reason an invitation may be refused, in one place.

        Returns a machine code rather than a boolean so the caller can tell
        the learner which of "revoked", "expired" or "exhausted" applies --
        and so the same decision cannot be re-derived slightly differently at
        a second call site.
        """
        if self.status != self.Status.ACTIVE:
            return "invitation_revoked"
        if not self.organization.is_open:
            return "organization_unavailable"
        if self.classroom_id and self.classroom.status != Classroom.Status.ACTIVE:
            return "class_unavailable"
        if self.expires_at is not None and self.expires_at <= now:
            return "invitation_expired"
        if self.max_uses and self.usage_count >= self.max_uses:
            return "invitation_exhausted"
        return None


class JoinRequest(BaseModel):
    """A learner's pending ask. Membership is created only on approval."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    invitation = models.ForeignKey(
        Invitation, on_delete=models.SET_NULL, related_name="join_requests", null=True, blank=True
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="join_requests")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="join_requests")
    classroom = models.ForeignKey(
        Classroom, on_delete=models.CASCADE, related_name="join_requests", null=True, blank=True
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="decided_join_requests",
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            # One pending request per learner per class. A learner tapping
            # "join" repeatedly must not produce a queue of identical requests
            # for a manager to wade through.
            models.UniqueConstraint(
                fields=("user", "classroom"),
                condition=Q(status="pending", classroom__isnull=False),
                name="unique_pending_class_join_request",
            ),
            models.UniqueConstraint(
                fields=("user", "organization"),
                condition=Q(status="pending", classroom__isnull=True),
                name="unique_pending_organization_join_request",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "status"), name="join_req_org_status_idx"),
            models.Index(fields=("classroom", "status"), name="join_req_class_status_idx"),
            models.Index(fields=("user", "status"), name="join_req_user_status_idx"),
        ]

    def __str__(self):
        return f"join:{self.public_id}"


class AdminRoleScope(BaseModel):
    """Which data a role assignment applies to.

    Deliberately its own table rather than columns on AdminUserRole, which
    carries UniqueConstraint(user, role): scope columns there would cap an
    account at one organization per role forever, and "this manager now also
    runs the second branch" is an ordinary request, not a schema migration.

    Absence of any scope row grants nothing. The migration backfills a GLOBAL
    row for every existing assignment, so today's platform admins keep exactly
    the reach they have -- but a new assignment that forgets its scope fails
    closed rather than silently becoming global.
    """

    class ScopeType(models.TextChoices):
        GLOBAL = "global", "Global"
        ORGANIZATION = "organization", "Organization"
        CLASS = "class", "Class"

    admin_user_role = models.ForeignKey(
        "admin_dashboard.AdminUserRole", on_delete=models.CASCADE, related_name="scopes"
    )
    scope_type = models.CharField(max_length=20, choices=ScopeType.choices, db_index=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="role_scopes",
        null=True,
        blank=True,
    )
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.CASCADE,
        related_name="role_scopes",
        null=True,
        blank=True,
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="granted_role_scopes",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ("scope_type",)
        constraints = [
            # A contradictory row -- "global, but for organization 7" -- would
            # make every scope decision ambiguous, so the shape is enforced
            # here rather than trusted to every writer.
            models.CheckConstraint(
                condition=(
                    Q(scope_type="global", organization__isnull=True, classroom__isnull=True)
                    | Q(scope_type="organization", organization__isnull=False, classroom__isnull=True)
                    | Q(scope_type="class", organization__isnull=True, classroom__isnull=False)
                ),
                name="admin_role_scope_shape_is_valid",
            ),
            models.UniqueConstraint(
                fields=("admin_user_role", "scope_type", "organization", "classroom"),
                name="unique_admin_role_scope",
            ),
            # SQL UNIQUE permits multiple NULL tuples on PostgreSQL.  These
            # partial constraints make duplicate GLOBAL and CLASS grants
            # impossible instead of merely harmless at query time.
            models.UniqueConstraint(
                fields=("admin_user_role",),
                condition=Q(scope_type="global"),
                name="unique_admin_role_global_scope",
            ),
            models.UniqueConstraint(
                fields=("admin_user_role", "organization"),
                condition=Q(scope_type="organization"),
                name="unique_admin_role_organization_scope",
            ),
            models.UniqueConstraint(
                fields=("admin_user_role", "classroom"),
                condition=Q(scope_type="class"),
                name="unique_admin_role_class_scope",
            ),
        ]
        indexes = [
            models.Index(fields=("organization",), name="role_scope_org_idx"),
            models.Index(fields=("classroom",), name="role_scope_class_idx"),
        ]

    def __str__(self):
        return f"{self.admin_user_role_id}:{self.scope_type}"
