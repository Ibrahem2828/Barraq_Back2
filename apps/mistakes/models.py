"""My Mistakes Notebook (دفتر أخطائي): every quiz error becomes visible progress.

Both models here are scheduled with the same small Leitner ladder
(`apps.mistakes.scheduling`) -- spaced review and effortful recall have a
real evidence base for retention, and neither needs a new review to be
*generated*: a mistake notebook entry is read from a quiz the learner
already took, and a flashcard is read from a summary Kholasa already made.
"""

from django.conf import settings
from django.db import models

from apps.common.models import SoftDeleteModel


class MistakeEntry(SoftDeleteModel):
    """One question the learner got wrong, pulled from a submitted attempt
    (or typed in by hand for a paper exam), with its own retry schedule."""

    class Category(models.TextChoices):
        NOT_UNDERSTOOD = 'not_understood', 'Did not understand'
        FORGOT = 'forgot', 'Forgot'
        RUSHED = 'rushed', 'Rushed'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        MASTERED = 'mastered', 'Mastered'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mistake_entries')
    # Set only for an imported mistake; unique together below makes
    # re-importing the same attempt a no-op instead of a duplicate.
    quiz_attempt = models.ForeignKey(
        'quizzes.QuizAttempt', on_delete=models.SET_NULL, related_name='mistake_entries', null=True, blank=True
    )
    question = models.ForeignKey(
        'quizzes.Question', on_delete=models.SET_NULL, related_name='mistake_entries', null=True, blank=True
    )
    subject = models.ForeignKey(
        'subjects.Subject', on_delete=models.SET_NULL, related_name='mistake_entries', null=True, blank=True
    )
    topic = models.CharField(max_length=255, blank=True)
    question_text = models.TextField()
    student_answer_text = models.TextField(blank=True)
    correct_answer_text = models.TextField(blank=True)
    explanation = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    # The Leitner box (0-based index into REVIEW_INTERVALS_DAYS); higher is
    # more consolidated. See apps.mistakes.scheduling.
    box = models.PositiveSmallIntegerField(default=0)
    next_review_at = models.DateTimeField(db_index=True)
    review_count = models.PositiveIntegerField(default=0)
    last_reviewed_at = models.DateTimeField(null=True, blank=True)
    mastered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('next_review_at',)
        constraints = [
            # A question already in the notebook is not re-imported; it is
            # reviewed instead. Only applies to imported rows (question set).
            models.UniqueConstraint(
                fields=('user', 'question'),
                condition=models.Q(question__isnull=False),
                name='unique_mistake_entry_per_question',
            )
        ]
        indexes = [models.Index(fields=('user', 'status', 'next_review_at'), name='mistake_user_due_idx')]

    def __str__(self):
        return f'{self.user_id}: {self.question_text[:40]}'


class FlashcardReviewState(SoftDeleteModel):
    """Spaced-review schedule for one flashcard inside a Kholasa summary.

    Flashcards live as plain JSON on ``Summary.flashcards`` (no model of
    their own); this is the one row per (user, summary, index) that turns
    "a card exists" into "a card is due".
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='flashcard_reviews')
    summary = models.ForeignKey('summaries.Summary', on_delete=models.CASCADE, related_name='review_states')
    flashcard_index = models.PositiveIntegerField()
    box = models.PositiveSmallIntegerField(default=0)
    next_review_at = models.DateTimeField(db_index=True)
    review_count = models.PositiveIntegerField(default=0)
    last_reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'summary', 'flashcard_index'), name='unique_flashcard_review_state'
            )
        ]
        indexes = [models.Index(fields=('user', 'next_review_at'), name='flashcard_review_due_idx')]

    def __str__(self):
        return f'{self.user_id}: {self.summary_id}#{self.flashcard_index}'
