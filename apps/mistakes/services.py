from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.quizzes.models import AttemptStatusChoices, QuizAttempt
from apps.summaries.models import Summary

from .models import FlashcardReviewState, MistakeEntry
from .scheduling import schedule


@transaction.atomic
def import_from_attempt(user, attempt_id):
    """Pulls every wrong answer of one submitted, owned attempt into the
    notebook. Idempotent: a question already in the notebook is skipped,
    never duplicated (see MistakeEntry's unique constraint)."""
    try:
        attempt = QuizAttempt.objects.select_related('quiz', 'quiz__subject').get(pk=attempt_id, user=user)
    except QuizAttempt.DoesNotExist as exc:
        raise ValidationError({'attempt': 'Attempt not found.'}) from exc
    if attempt.status != AttemptStatusChoices.SUBMITTED:
        raise ValidationError({'attempt': 'Only a submitted attempt can be imported.'})

    existing_question_ids = set(
        MistakeEntry.objects.filter(user=user, question__isnull=False).values_list('question_id', flat=True)
    )
    now = timezone.now()
    created = []
    wrong_answers = (
        attempt.answers.filter(is_correct=False)
        .select_related('question', 'selected_choice')
        .prefetch_related('question__choices')
    )
    for answer in wrong_answers:
        if answer.question_id in existing_question_ids:
            continue
        correct_choice = answer.question.choices.filter(is_correct=True).first()
        created.append(
            MistakeEntry(
                user=user,
                quiz_attempt=attempt,
                question=answer.question,
                subject=attempt.quiz.subject,
                topic=attempt.quiz.topic,
                question_text=answer.question.text,
                student_answer_text=answer.text_answer or getattr(answer.selected_choice, 'text', ''),
                correct_answer_text=getattr(correct_choice, 'text', ''),
                explanation=answer.question.explanation,
                next_review_at=now,
            )
        )
        existing_question_ids.add(answer.question_id)
    if created:
        MistakeEntry.objects.bulk_create(created)
    return created


def review_mistake(entry, *, result):
    schedule(entry, result=result, mastered_field='mastered_at')
    entry.save(update_fields=['box', 'status', 'next_review_at', 'review_count', 'last_reviewed_at', 'mastered_at', 'updated_at'])
    return entry


def review_flashcard(user, summary, flashcard_index, *, result):
    state, _ = FlashcardReviewState.objects.get_or_create(
        user=user, summary=summary, flashcard_index=flashcard_index, defaults={'next_review_at': timezone.now()}
    )
    schedule(state, result=result)
    state.save(update_fields=['box', 'next_review_at', 'review_count', 'last_reviewed_at', 'updated_at'])
    return state


def due_mistakes(user, *, limit):
    now = timezone.now()
    return list(
        MistakeEntry.objects.filter(user=user, status=MistakeEntry.Status.ACTIVE, next_review_at__lte=now)
        .select_related('subject')
        .order_by('next_review_at')[:limit]
    )


def due_flashcards(user, *, limit):
    """Flashcards due for review: any card never reviewed, plus any card
    whose schedule has come due. Computed lazily -- no row exists for a
    card until its first review, so "never reviewed" always means "due
    now".
    """
    now = timezone.now()
    reviewed = {
        (state.summary_id, state.flashcard_index): state
        for state in FlashcardReviewState.objects.filter(user=user).select_related('summary')
    }
    due = []
    summaries = Summary.objects.filter(user=user).exclude(flashcards=[]).only('id', 'title', 'flashcards')
    for summary in summaries:
        for index, card in enumerate(summary.flashcards or []):
            state = reviewed.get((summary.id, index))
            if state is None:
                due.append({'summary': summary, 'flashcard_index': index, 'card': card, 'next_review_at': None})
            elif state.next_review_at <= now:
                due.append(
                    {'summary': summary, 'flashcard_index': index, 'card': card, 'next_review_at': state.next_review_at}
                )
    # Rows with no schedule yet are the most overdue; sort them first.
    due.sort(key=lambda row: row['next_review_at'] or timezone.datetime.min.replace(tzinfo=now.tzinfo))
    return due[:limit]
