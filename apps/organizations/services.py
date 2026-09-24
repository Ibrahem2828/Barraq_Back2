"""Membership, invitation and join-request behaviour.

The invariants here are concurrent ones. Two managers approving the same
request, a learner double-tapping "join", a last seat taken twice -- each is
an ordinary sequence of events, not an attack, and each produces duplicate
rows if the check is a Python `if` over unlocked data. So the rules live in
database constraints and row locks, and this module's job is to turn the
resulting integrity errors into honest domain outcomes.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    ClassMembership,
    Classroom,
    Invitation,
    JoinRequest,
    Organization,
    OrganizationMembership,
)


class OrganizationError(ValidationError):
    """A validation error carrying a stable domain code.

    Phase 1's `domain_code` promotes this to the top of the error envelope so
    a client can tell `invitation_expired` from `invitation_revoked` instead
    of showing one generic failure for both.
    """

    def __init__(self, detail, *, code):
        self.domain_code = code
        super().__init__(detail)


INVITATION_MESSAGES = {
    "invitation_invalid": "رمز الدعوة غير صحيح.",
    "invitation_revoked": "تم إلغاء هذه الدعوة.",
    "invitation_expired": "انتهت صلاحية هذه الدعوة.",
    "invitation_exhausted": "تم استخدام هذه الدعوة بالكامل.",
    "organization_unavailable": "هذه المؤسسة غير متاحة حاليًا.",
    "class_unavailable": "هذا الصف غير متاح حاليًا.",
}


def resolve_invitation(*, token=None, code=None):
    """Find a usable invitation, or refuse with a specific reason.

    A missing invitation and a revoked one deliberately return the same
    `invitation_invalid` to an unauthenticated probe path? No -- they do not,
    and that is a considered choice: the learner who mistypes one character
    needs to know it was the code, and an invitation is a low-value secret
    behind a throttle. What is never revealed is anything *about* an
    organization whose invitation was not valid.
    """
    lookup = {}
    if token:
        lookup["token"] = token
    elif code:
        lookup["code"] = str(code).strip().upper()
    else:
        raise OrganizationError({"invitation": INVITATION_MESSAGES["invitation_invalid"]}, code="invitation_invalid")

    invitation = Invitation.objects.select_related("organization", "classroom").filter(**lookup).first()
    if invitation is None:
        raise OrganizationError({"invitation": INVITATION_MESSAGES["invitation_invalid"]}, code="invitation_invalid")
    reason = invitation.is_usable(now=timezone.now())
    if reason:
        raise OrganizationError({"invitation": INVITATION_MESSAGES[reason]}, code=reason)
    return invitation


def preview_invitation(invitation):
    """The only thing a stranger holding a code learns.

    Enough to answer "is this the class I was told to join" -- and nothing
    else. No member list, no manager contact details, no counts, no internal
    ids: a code that circulates further than intended must not become a
    directory of the school.
    """
    payload = {
        "organization": {
            "public_id": str(invitation.organization.public_id),
            "name": invitation.organization.name,
            "organization_type": invitation.organization.organization_type,
        },
        "classroom": None,
    }
    if invitation.classroom:
        payload["classroom"] = {
            "public_id": str(invitation.classroom.public_id),
            "name": invitation.classroom.name,
        }
    return payload


@transaction.atomic
def request_to_join(*, user, invitation):
    """Record a learner's ask. Creates no membership.

    Returns (join_request, created). Re-confirming is idempotent: the learner
    who taps twice, or reopens the link an hour later, gets the same pending
    request back rather than adding another one to a manager's queue.
    """
    existing_active = _active_membership_for(user, invitation)
    if existing_active is not None:
        raise OrganizationError({"membership": "أنت عضو بالفعل."}, code="membership_already_active")

    filters = {"user": user, "status": JoinRequest.Status.PENDING}
    if invitation.classroom_id:
        filters["classroom_id"] = invitation.classroom_id
    else:
        filters["organization_id"] = invitation.organization_id
        filters["classroom__isnull"] = True
    pending = JoinRequest.objects.filter(**filters).first()
    if pending is not None:
        return pending, False

    try:
        join_request = JoinRequest.objects.create(
            invitation=invitation,
            user=user,
            organization=invitation.organization,
            classroom=invitation.classroom,
        )
    except IntegrityError:
        # The partial unique index caught a request created concurrently by
        # the same learner. Return theirs rather than failing a tap.
        pending = JoinRequest.objects.filter(**filters).first()
        if pending is None:
            raise
        return pending, False
    return join_request, True


def _active_membership_for(user, invitation):
    if invitation.classroom_id:
        return ClassMembership.objects.filter(
            classroom_id=invitation.classroom_id, user=user, status=ClassMembership.Status.ACTIVE
        ).first()
    return OrganizationMembership.objects.filter(
        organization_id=invitation.organization_id,
        user=user,
        status=OrganizationMembership.Status.ACTIVE,
    ).first()


@transaction.atomic
def approve_join_request(*, join_request, approved_by):
    """Turn a pending request into membership.

    Idempotent by row lock: two managers clicking approve at the same instant
    both reach here, one wins the lock, and the second sees an
    already-approved request and returns it untouched rather than creating a
    second membership.

    Joining a class implies joining its organization -- a learner cannot be in
    10-A without being at the school -- so both memberships are established
    together, in one transaction.
    """
    join_request = JoinRequest.objects.select_for_update().get(pk=join_request.pk)
    if join_request.status == JoinRequest.Status.APPROVED:
        return join_request
    if join_request.status != JoinRequest.Status.PENDING:
        raise OrganizationError({"join_request": "لم يعد هذا الطلب قيد الانتظار."}, code="join_request_not_pending")

    organization_membership, _ = OrganizationMembership.objects.get_or_create(
        organization=join_request.organization,
        user=join_request.user,
        status=OrganizationMembership.Status.ACTIVE,
        defaults={
            "member_type": OrganizationMembership.MemberType.STUDENT,
            "joined_at": timezone.now(),
            "approved_by": approved_by,
        },
    )
    if join_request.classroom_id:
        ClassMembership.objects.get_or_create(
            classroom=join_request.classroom,
            user=join_request.user,
            status=ClassMembership.Status.ACTIVE,
            defaults={"joined_at": timezone.now(), "approved_by": approved_by},
        )

    if join_request.invitation_id:
        _consume_invitation(join_request.invitation_id)

    join_request.status = JoinRequest.Status.APPROVED
    join_request.decided_by = approved_by
    join_request.decided_at = timezone.now()
    join_request.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    return join_request


def _consume_invitation(invitation_id):
    """Increment usage under a row lock.

    `if count < max: count += 1` over unlocked data lets two concurrent
    redemptions both observe the last seat as free. The lock is what makes
    max_uses mean anything.
    """
    # of=("self",): classroom is nullable, so select_related() emits a LEFT
    # OUTER JOIN, and PostgreSQL refuses FOR UPDATE on the nullable side of
    # one. Only the invitation row needs the lock; the related rows are read.
    invitation = (
        Invitation.objects.select_for_update(of=("self",))
        .select_related("organization", "classroom")
        .get(pk=invitation_id)
    )
    # The learner-facing preview/confirm check happened before this
    # transaction. Recheck under the lock so a revocation, expiry or final
    # seat consumed by another approval cannot be bypassed by an older pending
    # request. Raising rolls back the membership work in approve_join_request.
    reason = invitation.is_usable(now=timezone.now())
    if reason:
        raise OrganizationError({"invitation": INVITATION_MESSAGES[reason]}, code=reason)
    invitation.usage_count += 1
    invitation.save(update_fields=["usage_count", "updated_at"])


@transaction.atomic
def reject_join_request(*, join_request, rejected_by):
    """Refuse a pending request.

    Never touches an existing membership: rejecting a request that was
    already approved would silently remove a learner from their class, which
    is a different action with different authorization, so it is refused.
    """
    join_request = JoinRequest.objects.select_for_update().get(pk=join_request.pk)
    if join_request.status == JoinRequest.Status.REJECTED:
        return join_request
    if join_request.status != JoinRequest.Status.PENDING:
        raise OrganizationError({"join_request": "لم يعد هذا الطلب قيد الانتظار."}, code="join_request_not_pending")
    join_request.status = JoinRequest.Status.REJECTED
    join_request.decided_by = rejected_by
    join_request.decided_at = timezone.now()
    join_request.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    return join_request


@transaction.atomic
def remove_class_member(*, membership, removed_by):
    """Remove a learner from a class, keeping the record.

    Status change rather than delete: their quiz attempts, AI results and
    support history all reference this membership's context, and a hard
    delete would make a learner who left mid-term look like one who was never
    there.
    """
    membership = ClassMembership.objects.select_for_update().get(pk=membership.pk)
    if membership.status != ClassMembership.Status.ACTIVE:
        return membership
    membership.status = ClassMembership.Status.REMOVED
    membership.removed_at = timezone.now()
    membership.save(update_fields=["status", "removed_at", "updated_at"])
    return membership


@transaction.atomic
def transfer_class_member(*, membership, target_classroom, moved_by):
    """Move a learner between classes of the same organization.

    Cross-organization transfer is deliberately not implemented: it would
    have to decide what happens to the learner's organization membership,
    their existing class history and any organization-scoped records, and
    that is a product policy nobody has set. Refusing is honest; inventing a
    rule and applying it to real learners is not.
    """
    membership = ClassMembership.objects.select_for_update().get(pk=membership.pk)
    if target_classroom.organization_id != membership.classroom.organization_id:
        raise OrganizationError(
            {"classroom": "النقل بين مؤسستين غير مدعوم."},
            code="cross_organization_transfer_unsupported",
        )
    if target_classroom.id == membership.classroom_id:
        return membership
    if target_classroom.status != Classroom.Status.ACTIVE:
        raise OrganizationError({"classroom": "الصف غير متاح."}, code="class_unavailable")

    membership.status = ClassMembership.Status.REMOVED
    membership.removed_at = timezone.now()
    membership.save(update_fields=["status", "removed_at", "updated_at"])
    moved, _ = ClassMembership.objects.get_or_create(
        classroom=target_classroom,
        user=membership.user,
        status=ClassMembership.Status.ACTIVE,
        defaults={"joined_at": timezone.now(), "approved_by": moved_by},
    )
    return moved


@transaction.atomic
def archive_organization(*, organization, archived_by=None):
    """Close an organization without destroying its history.

    Its classes stop accepting members and its invitations stop working, but
    every membership, result and ticket remains attached and auditable.
    """
    organization = Organization.objects.select_for_update().get(pk=organization.pk)
    organization.status = Organization.Status.ARCHIVED
    organization.save(update_fields=["status", "updated_at"])
    Classroom.objects.filter(organization=organization).update(
        status=Classroom.Status.ARCHIVED, updated_at=timezone.now()
    )
    Invitation.objects.filter(organization=organization, status=Invitation.Status.ACTIVE).update(
        status=Invitation.Status.REVOKED, updated_at=timezone.now()
    )
    return organization


@transaction.atomic
def archive_classroom(*, classroom):
    classroom = Classroom.objects.select_for_update().get(pk=classroom.pk)
    classroom.status = Classroom.Status.ARCHIVED
    classroom.save(update_fields=["status", "updated_at"])
    Invitation.objects.filter(classroom=classroom, status=Invitation.Status.ACTIVE).update(
        status=Invitation.Status.REVOKED, updated_at=timezone.now()
    )
    return classroom


def organization_overview(organization):
    """Counts for one organization, computed in the database.

    Aggregated per organization rather than globally and filtered afterwards:
    a total is as much of a leak as a list, and "filter it in the client" has
    never once been a boundary.
    """
    return {
        "active_students": OrganizationMembership.objects.filter(
            organization=organization,
            status=OrganizationMembership.Status.ACTIVE,
            member_type=OrganizationMembership.MemberType.STUDENT,
        ).count(),
        "active_classes": Classroom.objects.filter(organization=organization, status=Classroom.Status.ACTIVE).count(),
        "pending_join_requests": JoinRequest.objects.filter(
            organization=organization, status=JoinRequest.Status.PENDING
        ).count(),
        "active_invitations": Invitation.objects.filter(
            organization=organization, status=Invitation.Status.ACTIVE
        ).count(),
    }
