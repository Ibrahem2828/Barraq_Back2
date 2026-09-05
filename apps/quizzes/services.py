from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    AttemptStatusChoices,
    GenerationTypeChoices,
    QuestionBankItem,
    QuestionTypeChoices,
    Quiz,
    QuizAttempt,
    QuizLogActionChoices,
    QuizProgressLog,
    QuizStatusChoices,
    StudentAnswer,
)


def _log_quiz_event(user, action, quiz=None, attempt=None, metadata=None):
    return QuizProgressLog.objects.create(
        user=user,
        quiz=quiz,
        attempt=attempt,
        action=action,
        metadata=metadata or {},
    )


def _normalize_question_types(question_types):
    valid_types = {value for value, _label in QuestionTypeChoices.choices}
    normalized = [item for item in (question_types or []) if item in valid_types]
    return normalized or [QuestionTypeChoices.MCQ]


def _sync_quiz_questions_count(quiz):
    questions_count = quiz.questions.count()
    if quiz.questions_count != questions_count:
        quiz.questions_count = questions_count
        quiz.save(update_fields=['questions_count', 'updated_at'])
    return quiz


def _quantize_score(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _attempt_deadline(attempt):
    limit = attempt.quiz.time_limit_minutes
    if not limit:
        return None
    return attempt.started_at + timedelta(minutes=limit)


def _attempt_has_expired(attempt, *, grace_seconds=15):
    deadline = _attempt_deadline(attempt)
    return bool(deadline and timezone.now() > deadline + timedelta(seconds=grace_seconds))


def _create_question_bank_item(quiz, question, choices):
    QuestionBankItem.objects.create(
        subject=quiz.subject,
        created_by=quiz.user,
        text=question.text,
        question_type=question.question_type,
        difficulty_level=question.difficulty_level,
        explanation=question.explanation,
        metadata={
            'quiz_id': quiz.id,
            'topic': quiz.topic,
            'choices': [
                {
                    'text': choice.text,
                    'order': choice.order,
                    'is_correct': choice.is_correct,
                }
                for choice in choices
            ],
        },
        is_public=False,
        is_active=True,
    )


@transaction.atomic
def create_quiz(user, validated_data):
    validated_data.pop('question_types', None)
    generation_type = validated_data.get('generation_type', GenerationTypeChoices.MANUAL)
    if generation_type == GenerationTypeChoices.AI:
        raise ValidationError({
            'generation_type': 'Use POST /api/v1/ai/jobs/ with task_type=fahes_generate_quiz for AI quizzes.'
        })
    validated_data['questions_count'] = 0
    quiz = Quiz.objects.create(
        user=user,
        status=QuizStatusChoices.DRAFT,
        **validated_data,
    )
    _log_quiz_event(
        user=user,
        quiz=quiz,
        action=QuizLogActionChoices.QUIZ_CREATED,
        metadata={'generation_type': generation_type, 'questions_count': 0},
    )
    return quiz


@transaction.atomic
def update_quiz(quiz, validated_data):
    previous_status = quiz.status

    for field, value in validated_data.items():
        setattr(quiz, field, value)
    quiz.save()

    if previous_status != QuizStatusChoices.ARCHIVED and quiz.status == QuizStatusChoices.ARCHIVED:
        _log_quiz_event(
            user=quiz.user,
            quiz=quiz,
            action=QuizLogActionChoices.QUIZ_ARCHIVED,
        )
    return quiz


def _calculate_quiz_max_score(quiz):
    total_points = quiz.questions.aggregate(total=Sum('points'))['total'] or 0
    return _quantize_score(total_points)


@transaction.atomic
def start_quiz_attempt(user, quiz):
    if quiz.user_id != user.id:
        raise ValidationError('You cannot start an attempt for another user quiz.')
    if quiz.status != QuizStatusChoices.PUBLISHED:
        raise ValidationError('Only published quizzes can be started.')
    if not quiz.questions.exists():
        raise ValidationError('A quiz must contain at least one question before an attempt can start.')

    existing_attempt = (
        QuizAttempt.objects.select_for_update()
        .filter(user=user, quiz=quiz, status=AttemptStatusChoices.IN_PROGRESS)
        .order_by('-started_at')
        .first()
    )
    if existing_attempt and not _attempt_has_expired(existing_attempt):
        return existing_attempt
    if existing_attempt:
        submit_quiz_attempt(existing_attempt, [])

    attempt = QuizAttempt.objects.create(
        user=user,
        quiz=quiz,
        status=AttemptStatusChoices.IN_PROGRESS,
        started_at=timezone.now(),
        max_score=_calculate_quiz_max_score(quiz),
    )
    _log_quiz_event(
        user=user,
        quiz=quiz,
        attempt=attempt,
        action=QuizLogActionChoices.ATTEMPT_STARTED,
        metadata={'questions_count': quiz.questions_count},
    )
    return attempt


def _grade_answer(question, selected_choice=None, text_answer=''):
    if question.question_type in {QuestionTypeChoices.MCQ, QuestionTypeChoices.TRUE_FALSE}:
        if selected_choice is None:
            raise ValidationError({'selected_choice': 'This field is required.'})
        is_correct = bool(selected_choice.is_correct)
        points_awarded = Decimal(str(question.points if is_correct else 0))
        return is_correct, _quantize_score(points_awarded)

    normalized_answer = (text_answer or '').strip().lower()
    if not normalized_answer:
        raise ValidationError({'text_answer': 'This field is required.'})

    return False, Decimal('0.00')


@transaction.atomic
def submit_answer(attempt, question, selected_choice=None, text_answer=None):
    if attempt.status != AttemptStatusChoices.IN_PROGRESS:
        raise ValidationError('Answers can only be submitted for in-progress attempts.')
    if _attempt_has_expired(attempt):
        raise ValidationError('The quiz time limit has expired. Submit the attempt to receive the result.')
    if question.quiz_id != attempt.quiz_id:
        raise ValidationError({'question': 'Question does not belong to this quiz attempt.'})
    if selected_choice and selected_choice.question_id != question.id:
        raise ValidationError({'selected_choice': 'Choice does not belong to the question.'})

    is_correct, points_awarded = _grade_answer(
        question,
        selected_choice=selected_choice,
        text_answer=text_answer or '',
    )

    answer, _created = StudentAnswer.objects.update_or_create(
        attempt=attempt,
        question=question,
        defaults={
            'selected_choice': selected_choice,
            'text_answer': text_answer or '',
            'is_correct': is_correct,
            'points_awarded': points_awarded,
        },
    )

    _log_quiz_event(
        user=attempt.user,
        quiz=attempt.quiz,
        attempt=attempt,
        action=QuizLogActionChoices.ANSWER_SUBMITTED,
        metadata={
            'question_id': question.id,
            'is_correct': is_correct,
        },
    )
    return answer


def calculate_attempt_result(attempt):
    total_questions = attempt.quiz.questions.count()
    answers = list(
        attempt.answers.select_related('question', 'selected_choice').all()
    )
    answered_count = len(answers)
    correct_answers_count = sum(1 for answer in answers if answer.is_correct)
    wrong_answers_count = answered_count - correct_answers_count
    unanswered_count = max(total_questions - answered_count, 0)
    score = _quantize_score(
        sum(answer.points_awarded for answer in answers) if answers else Decimal('0.00')
    )
    max_score = _calculate_quiz_max_score(attempt.quiz)
    percentage = (
        _quantize_score((score / max_score) * Decimal('100'))
        if max_score > 0
        else Decimal('0.00')
    )

    return {
        'score': score,
        'max_score': max_score,
        'percentage': percentage,
        'correct_answers_count': correct_answers_count,
        'wrong_answers_count': wrong_answers_count,
        'unanswered_count': unanswered_count,
    }


@transaction.atomic
def submit_quiz_attempt(attempt, answers_data):
    if attempt.status == AttemptStatusChoices.SUBMITTED:
        raise ValidationError('This attempt has already been submitted.')
    if attempt.status != AttemptStatusChoices.IN_PROGRESS:
        raise ValidationError('Only in-progress attempts can be submitted.')

    expired = _attempt_has_expired(attempt)

    question_map = {
        question.id: question
        for question in attempt.quiz.questions.prefetch_related('choices').all()
    }

    # Answers delivered after the server-side deadline are ignored. Previously saved answers remain valid.
    for answer_data in ([] if expired else answers_data):
        question = question_map.get(answer_data['question'].id)
        if question is None:
            raise ValidationError({'question': 'Question does not belong to this quiz.'})

        selected_choice = answer_data.get('selected_choice')
        text_answer = answer_data.get('text_answer')
        submit_answer(
            attempt,
            question,
            selected_choice=selected_choice,
            text_answer=text_answer,
        )

    result = calculate_attempt_result(attempt)
    submitted_at = timezone.now()
    duration_seconds = max(int((submitted_at - attempt.started_at).total_seconds()), 0)

    attempt.status = AttemptStatusChoices.SUBMITTED
    attempt.submitted_at = submitted_at
    attempt.score = result['score']
    attempt.max_score = result['max_score']
    attempt.percentage = result['percentage']
    attempt.correct_answers_count = result['correct_answers_count']
    attempt.wrong_answers_count = result['wrong_answers_count']
    attempt.unanswered_count = result['unanswered_count']
    attempt.duration_seconds = duration_seconds
    attempt.save()

    _log_quiz_event(
        user=attempt.user,
        quiz=attempt.quiz,
        attempt=attempt,
        action=QuizLogActionChoices.ATTEMPT_SUBMITTED,
        metadata={
            'score': str(attempt.score),
            'max_score': str(attempt.max_score),
            'percentage': str(attempt.percentage),
        },
    )
    return attempt


@transaction.atomic
def archive_quiz(user, quiz):
    if quiz.user_id != user.id:
        raise ValidationError('You cannot archive another user quiz.')
    if quiz.status == QuizStatusChoices.ARCHIVED:
        return quiz

    quiz.status = QuizStatusChoices.ARCHIVED
    quiz.save(update_fields=['status', 'updated_at'])

    _log_quiz_event(
        user=user,
        quiz=quiz,
        action=QuizLogActionChoices.QUIZ_ARCHIVED,
    )
    return quiz


@transaction.atomic
def abandon_attempt(attempt):
    if attempt.status != AttemptStatusChoices.IN_PROGRESS:
        raise ValidationError('Only in-progress attempts can be abandoned.')

    attempt.status = AttemptStatusChoices.ABANDONED
    attempt.save(update_fields=['status', 'updated_at'])
    return attempt


def build_attempt_recommendations(attempt):
    recommendations = ['Review the questions you answered incorrectly.']

    if attempt.percentage < Decimal('50.00'):
        recommendations.append('Retake the quiz after reviewing the core explanation.')
    elif attempt.percentage < Decimal('80.00'):
        recommendations.append('Focus on medium and hard questions before the next attempt.')
    else:
        recommendations.append('Good result. Try a harder quiz to reinforce mastery.')

    return recommendations


def build_attempt_result_payload(attempt):
    if attempt.status != AttemptStatusChoices.SUBMITTED:
        raise ValidationError('Result is only available after the attempt is submitted.')

    answers_by_question_id = {
        answer.question_id: answer
        for answer in attempt.answers.select_related('selected_choice', 'question').all()
    }

    question_results = []
    for question in attempt.quiz.questions.prefetch_related('choices').order_by('order', 'id'):
        answer = answers_by_question_id.get(question.id)
        correct_choice = question.choices.filter(is_correct=True).order_by('order', 'id').first()

        question_results.append(
            {
                'question_id': question.id,
                'text': question.text,
                'question_type': question.question_type,
                'difficulty_level': question.difficulty_level,
                'order': question.order,
                'points': question.points,
                'choices': list(question.choices.all()),
                'correct_choice': correct_choice,
                'selected_choice': answer.selected_choice if answer else None,
                'text_answer': answer.text_answer if answer else '',
                'is_correct': answer.is_correct if answer else False,
                'explanation': question.explanation,
                'points_awarded': answer.points_awarded if answer else Decimal('0.00'),
            }
        )

    return {
        'attempt': attempt,
        'quiz': attempt.quiz,
        'answers': question_results,
        'correct_answers_count': attempt.correct_answers_count,
        'wrong_answers_count': attempt.wrong_answers_count,
        'unanswered_count': attempt.unanswered_count,
        'percentage': attempt.percentage,
        'recommendations': build_attempt_recommendations(attempt),
    }


@transaction.atomic
def publish_quiz(quiz):
    if quiz.generation_type != GenerationTypeChoices.MANUAL:
        raise ValidationError("AI quizzes are published by the validated AI materialization flow.")
    questions = quiz.questions.prefetch_related("choices").all()
    if not questions.exists():
        raise ValidationError("A quiz must contain at least one question before publishing.")
    for question in questions:
        if question.question_type in {QuestionTypeChoices.MCQ, QuestionTypeChoices.TRUE_FALSE}:
            choices = list(question.choices.all())
            if len(choices) < 2 or sum(1 for choice in choices if choice.is_correct) != 1:
                raise ValidationError({"questions": f"Question {question.id} must contain exactly one correct choice."})
    _sync_quiz_questions_count(quiz)
    quiz.status = QuizStatusChoices.PUBLISHED
    quiz.save(update_fields=["status", "updated_at"])
    return quiz
