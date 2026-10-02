from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from .models import GroupBan, GroupMembership, GroupSession, GroupSessionSummary, StudyGroup

#: A message is flagged automatically once this many distinct members
#: report it -- visible to the owner before it needs a platform escalation.
AUTO_FLAG_REPORT_THRESHOLD = 2


def active_membership(user, group):
    return GroupMembership.objects.filter(group=group, user=user, status=GroupMembership.Status.ACTIVE).first()


def require_membership(user, group):
    membership = active_membership(user, group)
    if membership is None:
        raise NotFound('Group not found.')
    return membership


def require_owner(user, group):
    membership = require_membership(user, group)
    if membership.role != GroupMembership.Role.OWNER:
        raise ValidationError('Only the group owner can do this.')
    return membership


def my_group_ids(user):
    return set(
        GroupMembership.objects.filter(user=user, status=GroupMembership.Status.ACTIVE).values_list('group_id', flat=True)
    )


@transaction.atomic
def create_group(user, *, name, max_members):
    group = StudyGroup.objects.create(name=name, created_by=user, max_members=max_members)
    GroupMembership.objects.create(group=group, user=user, role=GroupMembership.Role.OWNER)
    return group


@transaction.atomic
def join_group(user, *, invite_code):
    group = StudyGroup.objects.select_for_update().filter(invite_code=invite_code, is_active=True).first()
    if group is None:
        raise ValidationError({'invite_code': 'Invalid invite code.'})
    if GroupBan.objects.filter(group=group, user=user).exists():
        raise ValidationError({'invite_code': 'You cannot rejoin this group.'})
    existing = GroupMembership.objects.filter(group=group, user=user).order_by('-created_at').first()
    if existing and existing.status == GroupMembership.Status.ACTIVE:
        return group, existing
    active_count = GroupMembership.objects.filter(group=group, status=GroupMembership.Status.ACTIVE).count()
    if active_count >= group.max_members:
        raise ValidationError({'invite_code': 'This group is full.'})
    if existing:
        existing.status = GroupMembership.Status.ACTIVE
        existing.joined_at = timezone.now()
        existing.left_at = None
        existing.save(update_fields=['status', 'joined_at', 'left_at', 'updated_at'])
        return group, existing
    membership = GroupMembership.objects.create(group=group, user=user)
    return group, membership


@transaction.atomic
def leave_group(membership):
    if membership.role == GroupMembership.Role.OWNER:
        raise ValidationError('The owner cannot leave -- ban the group or transfer it is not supported yet.')
    membership.status = GroupMembership.Status.LEFT
    membership.left_at = timezone.now()
    membership.save(update_fields=['status', 'left_at', 'updated_at'])


@transaction.atomic
def ban_member(owner, group, target_user, *, reason=''):
    if target_user.id == owner.id:
        raise ValidationError('You cannot ban yourself.')
    membership = GroupMembership.objects.filter(group=group, user=target_user, status=GroupMembership.Status.ACTIVE).first()
    if membership is None:
        raise NotFound('Member not found.')
    membership.status = GroupMembership.Status.BANNED
    membership.left_at = timezone.now()
    membership.save(update_fields=['status', 'left_at', 'updated_at'])
    GroupBan.objects.get_or_create(group=group, user=target_user, defaults={'banned_by': owner, 'reason': reason})


def session_status(session):
    """The computed, server-authoritative state of a synced timer."""
    now = timezone.now()
    if session.status in (GroupSession.Status.COMPLETED, GroupSession.Status.CANCELLED):
        return {'status': session.status, 'remaining_seconds': 0}
    if session.status == GroupSession.Status.ACTIVE and session.started_at:
        elapsed = (now - session.started_at).total_seconds()
        remaining = max(0, session.duration_minutes * 60 - int(elapsed))
        return {'status': session.status, 'remaining_seconds': remaining}
    return {'status': session.status, 'remaining_seconds': session.duration_minutes * 60}


@transaction.atomic
def start_session(session):
    if session.status != GroupSession.Status.SCHEDULED:
        raise ValidationError('Only a scheduled session can be started.')
    session.status = GroupSession.Status.ACTIVE
    session.started_at = timezone.now()
    session.save(update_fields=['status', 'started_at', 'updated_at'])
    return session


@transaction.atomic
def end_session(session):
    if session.status != GroupSession.Status.ACTIVE:
        raise ValidationError('Only an active session can be ended.')
    now = timezone.now()
    session.status = GroupSession.Status.COMPLETED
    session.ended_at = now
    session.save(update_fields=['status', 'ended_at', 'updated_at'])
    duration_actual = max(0, int((now - session.started_at).total_seconds())) if session.started_at else 0
    tasks_completed = session.group.tasks.filter(is_done=True).count()
    summary, _created = GroupSessionSummary.objects.update_or_create(
        session=session, defaults={'tasks_completed_count': tasks_completed, 'duration_actual_seconds': duration_actual}
    )
    return summary
