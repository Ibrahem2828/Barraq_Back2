from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel, SoftDeleteModel


class DifficultyLevelChoices(models.TextChoices):
    EASY = 'easy', 'Easy'
    MEDIUM = 'medium', 'Medium'
    HARD = 'hard', 'Hard'


class QuizTypeChoices(models.TextChoices):
    PRACTICE = 'practice', 'Practice'
    EXAM = 'exam', 'Exam'
    QUICK = 'quick', 'Quick'


class GenerationTypeChoices(models.TextChoices):
    MANUAL = 'manual', 'Manual'
    AI = 'ai', 'AI'


class QuizStatusChoices(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    PUBLISHED = 'published', 'Published'
    ARCHIVED = 'archived', 'Archived'


class QuestionTypeChoices(models.TextChoices):
    MCQ = 'mcq', 'MCQ'
    TRUE_FALSE = 'true_false', 'True / False'
    SHORT_ANSWER = 'short_answer', 'Short Answer'


class AttemptStatusChoices(models.TextChoices):
    IN_PROGRESS = 'in_progress', 'In Progress'
    SUBMITTED = 'submitted', 'Submitted'
    ABANDONED = 'abandoned', 'Abandoned'


class QuizLogActionChoices(models.TextChoices):
    QUIZ_CREATED = 'quiz_created', 'Quiz Created'
    QUIZ_GENERATED = 'quiz_generated', 'Quiz Generated'
    ATTEMPT_STARTED = 'attempt_started', 'Attempt Started'
    ANSWER_SUBMITTED = 'answer_submitted', 'Answer Submitted'
    ATTEMPT_SUBMITTED = 'attempt_submitted', 'Attempt Submitted'
    QUIZ_ARCHIVED = 'quiz_archived', 'Quiz Archived'


class Quiz(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quizzes',
    )
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.SET_NULL,
        related_name='quizzes',
        null=True,
        blank=True,
    )
    subject = models.ForeignKey(
        'subjects.Subject',
        on_delete=models.CASCADE,
        related_name='quizzes',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    topic = models.TextField(blank=True)
    difficulty_level = models.CharField(
        max_length=20,
        choices=DifficultyLevelChoices.choices,
        default=DifficultyLevelChoices.MEDIUM,
    )
    quiz_type = models.CharField(
        max_length=20,
        choices=QuizTypeChoices.choices,
        default=QuizTypeChoices.PRACTICE,
    )
    generation_type = models.CharField(
        max_length=20,
        choices=GenerationTypeChoices.choices,
        default=GenerationTypeChoices.MANUAL,
    )
    status = models.CharField(
        max_length=20,
        choices=QuizStatusChoices.choices,
        default=QuizStatusChoices.DRAFT,
    )
    questions_count = models.PositiveIntegerField(default=0)
    time_limit_minutes = models.PositiveIntegerField(null=True, blank=True)
    ai_request_id = models.CharField(max_length=100, blank=True)
    # OneToOne, unlike ai_request_id, is enforced by the database: it makes a
    # duplicate materialization for the same AIJob impossible at the schema
    # level, matching the guarantee apps.analytics/summaries/audio already
    # have via their own `ai_job` field.
    ai_job = models.OneToOneField(
        "ai_integration.AIJob", on_delete=models.SET_NULL, null=True, blank=True, related_name="quiz"
    )

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['user', 'status'], name='quiz_user_status_idx'),
            models.Index(fields=['project', 'status', '-created_at'], name='quiz_project_status_idx'),
        ]

    def __str__(self):
        return f"{self.title} - {self.user.email}"


class Question(BaseModel):
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='questions',
    )
    text = models.TextField()
    question_type = models.CharField(
        max_length=20,
        choices=QuestionTypeChoices.choices,
        default=QuestionTypeChoices.MCQ,
    )
    difficulty_level = models.CharField(
        max_length=20,
        choices=DifficultyLevelChoices.choices,
        default=DifficultyLevelChoices.MEDIUM,
    )
    explanation = models.TextField(blank=True)
    order = models.PositiveIntegerField()
    points = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ('order', 'id')
        constraints = [
            models.UniqueConstraint(
                fields=['quiz', 'order'],
                name='unique_question_order_per_quiz',
            )
        ]

    def __str__(self):
        return f"Question {self.order} - {self.quiz.title}"


class Choice(BaseModel):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='choices',
    )
    text = models.TextField()
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ('order', 'id')
        constraints = [
            models.UniqueConstraint(
                fields=['question', 'order'],
                name='unique_choice_order_per_question',
            )
        ]

    def __str__(self):
        return f"Choice {self.order} - Question {self.question_id}"


class QuizAttempt(SoftDeleteModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quiz_attempts',
    )
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='attempts',
    )
    status = models.CharField(
        max_length=20,
        choices=AttemptStatusChoices.choices,
        default=AttemptStatusChoices.IN_PROGRESS,
    )
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    max_score = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    correct_answers_count = models.PositiveIntegerField(default=0)
    wrong_answers_count = models.PositiveIntegerField(default=0)
    unanswered_count = models.PositiveIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ('-started_at',)
        indexes = [
            models.Index(fields=['user', 'quiz', 'status'], name='qa_user_quiz_status_idx'),
        ]

    def __str__(self):
        return f"Attempt {self.id} - {self.quiz.title}"


class StudentAnswer(BaseModel):
    attempt = models.ForeignKey(
        QuizAttempt,
        on_delete=models.CASCADE,
        related_name='answers',
    )
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='student_answers',
    )
    selected_choice = models.ForeignKey(
        Choice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='selected_answers',
    )
    text_answer = models.TextField(blank=True)
    is_correct = models.BooleanField(default=False)
    points_awarded = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
    )

    class Meta:
        ordering = ('question__order', 'id')
        constraints = [
            models.UniqueConstraint(
                fields=['attempt', 'question'],
                name='unique_answer_per_attempt_question',
            )
        ]

    def __str__(self):
        return f"Answer {self.id} - Attempt {self.attempt_id}"


class QuestionBankItem(BaseModel):
    subject = models.ForeignKey(
        'subjects.Subject',
        on_delete=models.CASCADE,
        related_name='question_bank_items',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='question_bank_items',
    )
    text = models.TextField()
    question_type = models.CharField(
        max_length=20,
        choices=QuestionTypeChoices.choices,
        default=QuestionTypeChoices.MCQ,
    )
    difficulty_level = models.CharField(
        max_length=20,
        choices=DifficultyLevelChoices.choices,
        default=DifficultyLevelChoices.MEDIUM,
    )
    explanation = models.TextField(blank=True)
    metadata = models.JSONField(blank=True, null=True)
    is_public = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f"Question Bank Item {self.id} - {self.subject.name}"


class QuizProgressLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quiz_progress_logs',
    )
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='progress_logs',
    )
    attempt = models.ForeignKey(
        QuizAttempt,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='progress_logs',
    )
    action = models.CharField(max_length=30, choices=QuizLogActionChoices.choices)
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f"{self.action} - {self.user.email}"
