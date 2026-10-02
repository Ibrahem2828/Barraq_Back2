from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.organizations import scope as scope_policy
from apps.organizations.models import ClassMembership, Classroom

from .models import ClassQuiz, ClassQuizAnswer, ClassQuizAttempt, ClassQuizQuestion

VIEW_PERMISSION = 'class_work.view'
MANAGE_PERMISSION = 'class_work.manage'


# -- scope (mirrors apps.organizations.library's staff/student split) --------
def scope_rows_for_staff(user, queryset, permission, *, classroom_field='classroom'):
    organizations = scope_policy._normalize(scope_policy.accessible_organization_ids(user, permission))
    if scope_policy.is_unrestricted(organizations):
        return queryset
    classrooms = scope_policy._normalize(scope_policy.accessible_classroom_ids(user, permission))
    class_ids = [] if scope_policy.is_unrestricted(classrooms) else list(classrooms or [])
    return queryset.filter(**{f'{classroom_field}_id__in': class_ids})


def resolve_classroom_for_write(user, classroom_public_id, *, permission=MANAGE_PERMISSION):
    classroom = Classroom.objects.select_related('organization').filter(public_id=classroom_public_id).first()
    if classroom is None or classroom.status != Classroom.Status.ACTIVE:
        raise ValidationError({'classroom': 'Unknown class.'})
    try:
        scope_policy.assert_classroom_allowed(user, classroom, permission)
    except scope_policy.ScopeDenied as exc:
        raise ValidationError({'classroom': 'Unknown class.'}) from exc
    return classroom


def student_classroom_ids(user):
    return set(
        ClassMembership.objects.filter(
            user=user, status=ClassMembership.Status.ACTIVE, classroom__status=Classroom.Status.ACTIVE
        ).values_list('classroom_id', flat=True)
    )


def assert_student_in_classroom(user, classroom_id):
    if classroom_id not in student_classroom_ids(user):
        raise NotFound('Class not found.')


# -- class quiz grading -------------------------------------------------------
def quiz_max_score(class_quiz):
    return sum((question.points for question in class_quiz.questions.all()), start=0)


def start_class_quiz_attempt(student, class_quiz):
    if class_quiz.status != ClassQuiz.Status.PUBLISHED:
        raise ValidationError('Only a published quiz can be started.')
    assert_student_in_classroom(student, class_quiz.classroom_id)
    existing = (
        ClassQuizAttempt.objects.select_for_update()
        .filter(student=student, class_quiz=class_quiz, status=ClassQuizAttempt.Status.IN_PROGRESS)
        .order_by('-started_at')
        .first()
    )
    if existing:
        return existing
    return ClassQuizAttempt.objects.create(
        student=student, class_quiz=class_quiz, max_score=Decimal(str(quiz_max_score(class_quiz)))
    )


def _grade_choice_answer(question, selected_choice):
    if selected_choice is None:
        raise ValidationError({'selected_choice': 'This field is required.'})
    if selected_choice.question_id != question.id:
        raise ValidationError({'selected_choice': 'Choice does not belong to the question.'})
    is_correct = bool(selected_choice.is_correct)
    return is_correct, Decimal(question.points) if is_correct else Decimal('0')


@transaction.atomic
def submit_class_quiz_answer(attempt, question, *, selected_choice=None, text_answer=''):
    if attempt.status != ClassQuizAttempt.Status.IN_PROGRESS:
        raise ValidationError('Answers can only be submitted for an in-progress attempt.')
    if question.class_quiz_id != attempt.class_quiz_id:
        raise ValidationError({'question': 'Question does not belong to this attempt.'})

    if question.question_type in (ClassQuizQuestion.QuestionType.MCQ, ClassQuizQuestion.QuestionType.TRUE_FALSE):
        is_correct, points_awarded = _grade_choice_answer(question, selected_choice)
    else:
        if not (text_answer or '').strip():
            raise ValidationError({'text_answer': 'This field is required.'})
        is_correct, points_awarded = None, Decimal('0')
        selected_choice = None

    answer, _created = ClassQuizAnswer.objects.update_or_create(
        attempt=attempt,
        question=question,
        defaults={
            'selected_choice': selected_choice,
            'text_answer': text_answer or '',
            'is_correct': is_correct,
            'points_awarded': points_awarded,
        },
    )
    return answer


def _recompute_attempt_totals(attempt):
    answers = list(attempt.answers.all())
    attempt.score = sum((answer.points_awarded for answer in answers), start=Decimal('0'))
    attempt.fully_graded = all(answer.is_correct is not None for answer in answers) and len(answers) == attempt.class_quiz.questions.count()
    attempt.percentage = (
        (attempt.score / attempt.max_score * 100) if attempt.fully_graded and attempt.max_score > 0 else None
    )
    attempt.save(update_fields=['score', 'fully_graded', 'percentage', 'updated_at'])
    return attempt


@transaction.atomic
def submit_class_quiz_attempt(attempt):
    if attempt.status != ClassQuizAttempt.Status.IN_PROGRESS:
        raise ValidationError('Only an in-progress attempt can be submitted.')
    attempt.status = ClassQuizAttempt.Status.SUBMITTED
    attempt.submitted_at = timezone.now()
    attempt.save(update_fields=['status', 'submitted_at', 'updated_at'])
    return _recompute_attempt_totals(attempt)


@transaction.atomic
def grade_written_answer(answer, *, points_awarded, teacher):
    question = answer.question
    if question.question_type != ClassQuizQuestion.QuestionType.WRITTEN:
        raise ValidationError('Only a written answer can be graded by hand.')
    points_awarded = max(Decimal('0'), min(Decimal(str(points_awarded)), Decimal(question.points)))
    answer.points_awarded = points_awarded
    answer.is_correct = points_awarded > 0
    answer.save(update_fields=['points_awarded', 'is_correct', 'updated_at'])
    _recompute_attempt_totals(answer.attempt)
    return answer
