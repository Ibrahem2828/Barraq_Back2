"""Student performance for organization supervisors.

What a school asks of the platform: which of my students are working, how
they score, and how they use the AI characters. Everything here is read
through the same tenant boundary as the rest of the dashboard -- a scoped
manager sees the students of the organizations (or classes) it was granted,
never a platform total -- and only *metadata* about AI use (character,
task, status, time), never a student's prompts, sources or answers.

The population is organization students: active STUDENT memberships in a
reachable organization, plus active members of a reachable class who are
students somewhere. Independent learners belong to no tenant and are not
part of any school's report.
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, F, IntegerField, Max, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.ai_integration.models import AIJob
from apps.organizations import scope as scope_policy
from apps.organizations.models import ClassMembership, Classroom, Organization, OrganizationMembership
from apps.quizzes.models import AttemptStatusChoices, QuizAttempt

User = get_user_model()

PERMISSION = 'students.view'
DEFAULT_PERIOD_DAYS = 30
MAX_PERIOD_DAYS = 365
#: Average below this (percent) marks a student as needing attention.
ATTENTION_THRESHOLD = 50
CHARACTERS = [choice for choice, _ in AIJob.Character.choices]
ORDERING_FIELDS = {
    'name': 'full_name',
    'email': 'email',
    'quizzes': 'quizzes_submitted',
    'score': 'average_score',
    'ai': 'ai_requests',
    'last_quiz': 'last_quiz_at',
    'last_ai': 'last_ai_at',
}


def period_start(params):
    """Start of the reporting window (`days`, default 30, 1..365)."""
    raw = params.get('days', DEFAULT_PERIOD_DAYS)
    try:
        days = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError({'days': 'A whole number of days is required.'}) from exc
    days = min(max(days, 1), MAX_PERIOD_DAYS)
    return days, timezone.now() - timedelta(days=days)


def _reach(actor):
    organizations = scope_policy._normalize(scope_policy.accessible_organization_ids(actor, PERMISSION))
    classrooms = scope_policy._normalize(scope_policy.accessible_classroom_ids(actor, PERMISSION))
    return organizations, classrooms


def _resolve_filters(actor, params, organizations, classrooms):
    """The organization/class the caller narrowed to, checked against reach.

    One message for "does not exist" and "not yours", as elsewhere: a
    distinct one would confirm a tenant the caller may not know about.
    """
    organization = classroom = None
    if params.get('organization'):
        organization = Organization.objects.filter(public_id=_uuid(params['organization'], 'organization')).first()
        reachable = organization is not None and (
            scope_policy.is_unrestricted(organizations)
            or organization.id in organizations
            or (
                not scope_policy.is_unrestricted(classrooms)
                and Classroom.objects.filter(organization=organization, id__in=classrooms).exists()
            )
        )
        if not reachable:
            raise NotFound('Unknown organization.')
    if params.get('classroom'):
        classroom = Classroom.objects.filter(public_id=_uuid(params['classroom'], 'classroom')).first()
        reachable = classroom is not None and (
            scope_policy.is_unrestricted(classrooms) or classroom.id in classrooms
        )
        if not reachable or (organization is not None and classroom.organization_id != organization.id):
            raise NotFound('Unknown class.')
    return organization, classroom


def _uuid(value, field):
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValidationError({field: 'A valid id is required.'}) from exc


def student_population(actor, params):
    """Students the caller may report on, narrowed by the request filters."""
    organizations, classrooms = _reach(actor)
    organization, classroom = _resolve_filters(actor, params, organizations, classrooms)

    student_memberships = OrganizationMembership.objects.filter(
        status=OrganizationMembership.Status.ACTIVE,
        member_type=OrganizationMembership.MemberType.STUDENT,
    )
    class_memberships = ClassMembership.objects.filter(status=ClassMembership.Status.ACTIVE)

    if classroom is not None:
        # A class report: its active members who are students.
        ids = class_memberships.filter(classroom=classroom).values('user_id')
        return User.objects.filter(id__in=ids, pk__in=student_memberships.values('user_id'))

    if scope_policy.is_unrestricted(organizations):
        memberships = student_memberships
        if organization is not None:
            memberships = memberships.filter(organization=organization)
        return User.objects.filter(id__in=memberships.values('user_id'))

    by_organization = student_memberships.filter(organization_id__in=organizations or [])
    reachable_classes = classrooms if not scope_policy.is_unrestricted(classrooms) else None
    by_class = class_memberships.filter(classroom_id__in=reachable_classes or [])
    if organization is not None:
        by_organization = by_organization.filter(organization=organization)
        by_class = by_class.filter(classroom__organization=organization)
    return User.objects.filter(
        Q(id__in=by_organization.values('user_id'))
        | Q(id__in=by_class.values('user_id'), pk__in=student_memberships.values('user_id'))
    )


def _per_user(queryset, expression):
    return Subquery(
        queryset.filter(user_id=OuterRef('pk')).order_by().values('user_id').annotate(value=expression).values('value')[:1]
    )


def _attempts(since):
    return QuizAttempt.objects.filter(status=AttemptStatusChoices.SUBMITTED, submitted_at__gte=since)


def _jobs(since):
    return AIJob.objects.filter(created_at__gte=since)


def annotate_metrics(users, since):
    attempts = _attempts(since)
    jobs = _jobs(since)
    zero = Value(0, output_field=IntegerField())
    return users.annotate(
        quizzes_submitted=Coalesce(_per_user(attempts, Count('id')), zero),
        average_score=_per_user(attempts, Avg('percentage')),
        last_quiz_at=_per_user(attempts, Max('submitted_at')),
        ai_requests=Coalesce(_per_user(jobs, Count('id')), zero),
        ai_completed=Coalesce(_per_user(jobs.filter(status=AIJob.Status.COMPLETED), Count('id')), zero),
        ai_failed=Coalesce(_per_user(jobs.filter(status=AIJob.Status.FAILED), Count('id')), zero),
        last_ai_at=_per_user(jobs, Max('created_at')),
    )


def apply_ordering(users, params):
    raw = params.get('ordering') or '-last_ai'
    descending = raw.startswith('-')
    field = ORDERING_FIELDS.get(raw.lstrip('-'))
    if field is None:
        raise ValidationError({'ordering': f"Use one of: {', '.join(sorted(ORDERING_FIELDS))} (prefix '-' to reverse)."})
    # Students with no activity sort last either way, not first.
    order = F(field).desc(nulls_last=True) if descending else F(field).asc(nulls_last=True)
    return users.order_by(order, 'id')


def apply_search(users, params):
    term = (params.get('search') or '').strip()
    if term:
        users = users.filter(Q(full_name__icontains=term) | Q(email__icontains=term))
    return users


def characters_by_user(user_ids, since):
    counts = {user_id: dict.fromkeys(CHARACTERS, 0) for user_id in user_ids}
    rows = _jobs(since).filter(user_id__in=user_ids).values('user_id', 'character').annotate(count=Count('id'))
    for row in rows:
        counts[row['user_id']][row['character']] = row['count']
    return counts


def placements_by_user(actor, user_ids):
    """Organization and class names for each row, limited to what the caller reaches."""
    organizations, classrooms = _reach(actor)
    org_rows = OrganizationMembership.objects.filter(
        user_id__in=user_ids,
        status=OrganizationMembership.Status.ACTIVE,
        member_type=OrganizationMembership.MemberType.STUDENT,
    ).select_related('organization')
    class_rows = ClassMembership.objects.filter(
        user_id__in=user_ids, status=ClassMembership.Status.ACTIVE
    ).select_related('classroom', 'classroom__organization')
    if not scope_policy.is_unrestricted(organizations):
        visible_class_ids = classrooms if not scope_policy.is_unrestricted(classrooms) else None
        class_rows = class_rows.filter(classroom_id__in=visible_class_ids or [])
        visible_org_ids = set(organizations or []) | {row.classroom.organization_id for row in class_rows}
        org_rows = org_rows.filter(organization_id__in=visible_org_ids)
    placements = {user_id: {'organizations': [], 'classes': []} for user_id in user_ids}
    for row in org_rows:
        placements[row.user_id]['organizations'].append(
            {'public_id': str(row.organization.public_id), 'name': row.organization.name}
        )
    for row in class_rows:
        placements[row.user_id]['classes'].append(
            {'public_id': str(row.classroom.public_id), 'name': row.classroom.name}
        )
    return placements


def last_activity(row):
    moments = [moment for moment in (row.last_quiz_at, row.last_ai_at) if moment is not None]
    return max(moments) if moments else None


def build_rows(actor, users, since):
    users = list(users)
    ids = [user.id for user in users]
    characters = characters_by_user(ids, since)
    placements = placements_by_user(actor, ids)
    return [
        {
            'id': user.id,
            'full_name': user.full_name,
            'email': user.email,
            'organizations': placements[user.id]['organizations'],
            'classes': placements[user.id]['classes'],
            'quizzes_submitted': user.quizzes_submitted,
            'average_score': _round(user.average_score),
            'last_quiz_at': user.last_quiz_at,
            'ai_requests': user.ai_requests,
            'ai_completed': user.ai_completed,
            'ai_failed': user.ai_failed,
            'ai_by_character': characters[user.id],
            'last_ai_at': user.last_ai_at,
            'last_activity_at': last_activity(user),
            'needs_attention': _needs_attention(user),
        }
        for user in users
    ]


def _round(value):
    return None if value is None else round(float(value), 1)


def _needs_attention(user):
    """No activity in the window, or a failing average."""
    if not user.quizzes_submitted and not user.ai_requests:
        return True
    return user.average_score is not None and float(user.average_score) < ATTENTION_THRESHOLD


def build_summary(actor, users, days, since):
    population = annotate_metrics(users, since)
    ids = list(users.values_list('id', flat=True))
    attempts = _attempts(since).filter(user_id__in=ids)
    jobs = _jobs(since).filter(user_id__in=ids)
    rows = list(population.values('quizzes_submitted', 'ai_requests', 'average_score'))
    buckets = {'below_50': 0, '50_69': 0, '70_84': 0, '85_plus': 0, 'no_quizzes': 0}
    attention = 0
    for row in rows:
        score = row['average_score']
        if score is None:
            buckets['no_quizzes'] += 1
        elif score < 50:
            buckets['below_50'] += 1
        elif score < 70:
            buckets['50_69'] += 1
        elif score < 85:
            buckets['70_84'] += 1
        else:
            buckets['85_plus'] += 1
        if (not row['quizzes_submitted'] and not row['ai_requests']) or (
            score is not None and float(score) < ATTENTION_THRESHOLD
        ):
            attention += 1
    by_character = dict.fromkeys(CHARACTERS, 0)
    for row in jobs.values('character').annotate(count=Count('id')):
        by_character[row['character']] = row['count']
    totals = attempts.aggregate(count=Count('id'), average=Avg('percentage'))
    return {
        'period_days': days,
        'students_count': len(rows),
        'active_students': sum(1 for row in rows if row['quizzes_submitted'] or row['ai_requests']),
        'needs_attention': attention,
        'quizzes_submitted': totals['count'],
        'average_score': _round(totals['average']),
        'ai_requests': jobs.count(),
        'ai_completed': jobs.filter(status=AIJob.Status.COMPLETED).count(),
        'ai_failed': jobs.filter(status=AIJob.Status.FAILED).count(),
        'ai_by_character': by_character,
        'score_distribution': buckets,
    }


def build_detail(actor, users, user_id, days, since):
    user = annotate_metrics(users.filter(pk=user_id), since).first()
    if user is None:
        raise NotFound('Unknown student.')
    row = build_rows(actor, [user], since)[0]
    attempts = (
        _attempts(since)
        .filter(user_id=user.id)
        .select_related('quiz', 'quiz__subject')
        .order_by('-submitted_at')[:20]
    )
    jobs = _jobs(since).filter(user_id=user.id).order_by('-created_at')[:20]
    row.update(
        {
            'period_days': days,
            'recent_attempts': [
                {
                    'quiz_title': attempt.quiz.title,
                    'subject': getattr(attempt.quiz.subject, 'name', None),
                    'percentage': _round(attempt.percentage),
                    'correct_answers_count': attempt.correct_answers_count,
                    'wrong_answers_count': attempt.wrong_answers_count,
                    'unanswered_count': attempt.unanswered_count,
                    'duration_seconds': attempt.duration_seconds,
                    'submitted_at': attempt.submitted_at,
                }
                for attempt in attempts
            ],
            # What the student asked for and when -- never the content.
            'recent_ai_activity': [
                {
                    'character': job.character,
                    'task_type': job.task_type,
                    'status': job.status,
                    'created_at': job.created_at,
                    'completed_at': job.completed_at,
                }
                for job in jobs
            ],
        }
    )
    return row
