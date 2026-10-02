from django.db import transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.class_work.services import scope_rows_for_staff, student_classroom_ids
from apps.organizations import scope as scope_policy
from apps.organizations.models import Classroom

from .models import HelpfulVote, KnowledgeThread, SavedThread

VIEW_PERMISSION = 'knowledge.view'
MODERATE_PERMISSION = 'knowledge.moderate'

__all__ = [
    'MODERATE_PERMISSION',
    'VIEW_PERMISSION',
    'accept_reply',
    'can_moderate',
    'reachable_classroom_ids',
    'require_edit_rights',
    'resolve_classroom_for_student',
    'scope_rows_for_staff',
    'thread_or_404_for_student',
    'toggle_helpful',
    'toggle_saved',
    'visible_threads_for_student',
]


def can_moderate(user, classroom):
    """Whether `user` holds a knowledge.moderate grant over this classroom
    -- also the fact fixed onto a post at creation as
    `is_teacher_content`/`is_teacher_reply` (a student's own moderation
    reach is empty, so this is a clean "is this a teacher" signal too)."""
    organizations = scope_policy._normalize(scope_policy.accessible_organization_ids(user, MODERATE_PERMISSION))
    if scope_policy.is_unrestricted(organizations) or classroom.organization_id in (organizations or set()):
        return True
    classrooms = scope_policy._normalize(scope_policy.accessible_classroom_ids(user, MODERATE_PERMISSION))
    return scope_policy.is_unrestricted(classrooms) or classroom.id in (classrooms or set())


def reachable_classroom_ids(user):
    """Every classroom `user` may read and post in here: their own active
    memberships, *plus* any classroom their knowledge.view staff grant
    reaches -- a teacher posting an explanation is not a student of their
    own class, so membership alone would shut them out of the exact
    feature they are meant to use."""
    ids = set(student_classroom_ids(user))
    organizations = scope_policy._normalize(scope_policy.accessible_organization_ids(user, VIEW_PERMISSION))
    classrooms = scope_policy._normalize(scope_policy.accessible_classroom_ids(user, VIEW_PERMISSION))
    if scope_policy.is_unrestricted(organizations) or scope_policy.is_unrestricted(classrooms):
        ids |= set(Classroom.objects.filter(status=Classroom.Status.ACTIVE).values_list('id', flat=True))
        return ids
    if organizations:
        ids |= set(Classroom.objects.filter(organization_id__in=organizations).values_list('id', flat=True))
    if classrooms:
        ids |= set(classrooms)
    return ids


def resolve_classroom_for_student(user, classroom_public_id):
    classroom = Classroom.objects.filter(public_id=classroom_public_id, status=Classroom.Status.ACTIVE).first()
    if classroom is None or classroom.id not in reachable_classroom_ids(user):
        raise NotFound('Class not found.')
    return classroom


def require_edit_rights(user, thread):
    if thread.author_id == user.id or can_moderate(user, thread.classroom):
        return
    raise PermissionDenied('Only the author or a class moderator can change this.')


@transaction.atomic
def accept_reply(user, thread, reply):
    if reply.thread_id != thread.id:
        raise ValidationError({'reply': 'This reply does not belong to the thread.'})
    if thread.author_id != user.id and not can_moderate(user, thread.classroom):
        raise PermissionDenied('Only the thread author or a class moderator can accept a reply.')
    thread.accepted_reply = reply
    thread.status = KnowledgeThread.Status.RESOLVED
    thread.save(update_fields=['accepted_reply', 'status', 'updated_at'])
    return thread


def toggle_helpful(user, reply):
    """Returns (is_helpful_now, helpful_count)."""
    deleted, _ = HelpfulVote.objects.filter(user=user, reply=reply).delete()
    if deleted:
        reply.helpful_count = max(0, reply.helpful_count - 1)
        reply.save(update_fields=['helpful_count', 'updated_at'])
        return False, reply.helpful_count
    HelpfulVote.objects.create(user=user, reply=reply)
    reply.helpful_count += 1
    reply.save(update_fields=['helpful_count', 'updated_at'])
    return True, reply.helpful_count


def toggle_saved(user, thread):
    deleted, _ = SavedThread.objects.filter(user=user, thread=thread).delete()
    if deleted:
        return False
    SavedThread.objects.create(user=user, thread=thread)
    return True


def visible_threads_for_student(user):
    return KnowledgeThread.objects.filter(classroom_id__in=reachable_classroom_ids(user))


def thread_or_404_for_student(user, thread_id):
    thread = visible_threads_for_student(user).filter(pk=thread_id).first()
    if thread is None:
        raise NotFound('Thread not found.')
    return thread
