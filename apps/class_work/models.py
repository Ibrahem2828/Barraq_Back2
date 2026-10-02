"""صفي (My Class): announcements, a calendar, assignments and teacher-made
quizzes, all scoped to one Classroom.

Visibility follows the same two boundaries as the Classroom Shared Library
(``apps.organizations.library``): staff reach these through their admin
scope (``class_work.view`` / ``class_work.manage``), students through an
active ``ClassMembership`` -- never through ownership, since none of this
belongs to the student who reads it.
"""

from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel, SoftDeleteModel


def assignment_submission_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    now = timezone.now()
    return f'assignment_submissions/{instance.assignment_id}/{instance.student_id}/{now:%Y}/{uuid4().hex}{extension}'


class ClassAnnouncement(BaseModel):
    classroom = models.ForeignKey('organizations.Classroom', on_delete=models.CASCADE, related_name='announcements')
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    pinned = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True
    )

    class Meta:
        ordering = ('-pinned', '-created_at')
        indexes = [models.Index(fields=('classroom', '-pinned', '-created_at'), name='announcement_classroom_idx')]

    def __str__(self):
        return self.title


class ClassEvent(BaseModel):
    """One row of the class calendar -- a lesson, exam or other date."""

    class EventType(models.TextChoices):
        LESSON = 'lesson', 'Lesson'
        EXAM = 'exam', 'Exam'
        OTHER = 'other', 'Other'

    classroom = models.ForeignKey('organizations.Classroom', on_delete=models.CASCADE, related_name='events')
    title = models.CharField(max_length=255)
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.LESSON)
    description = models.TextField(blank=True)
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True
    )

    class Meta:
        ordering = ('start_at',)
        indexes = [models.Index(fields=('classroom', 'start_at'), name='event_classroom_start_idx')]

    def __str__(self):
        return self.title


class ClassAssignment(BaseModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        CLOSED = 'closed', 'Closed'

    classroom = models.ForeignKey('organizations.Classroom', on_delete=models.CASCADE, related_name='assignments')
    subject = models.ForeignKey(
        'subjects.Subject', on_delete=models.SET_NULL, related_name='class_assignments', null=True, blank=True
    )
    title = models.CharField(max_length=255)
    instructions = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    allow_late = models.BooleanField(default=True)
    max_points = models.PositiveIntegerField(default=100)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True
    )

    class Meta:
        ordering = ('due_at', '-created_at')
        indexes = [models.Index(fields=('classroom', 'status'), name='assignment_status_idx')]

    def __str__(self):
        return self.title


class AssignmentSubmission(BaseModel):
    class Status(models.TextChoices):
        SUBMITTED = 'submitted', 'Submitted'
        LATE = 'late', 'Late'
        GRADED = 'graded', 'Graded'

    assignment = models.ForeignKey(ClassAssignment, on_delete=models.CASCADE, related_name='submissions')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assignment_submissions'
    )
    text_response = models.TextField(blank=True)
    file = models.FileField(upload_to=assignment_submission_upload_to, null=True, blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    mime_type = models.CharField(max_length=120, blank=True)
    extension = models.CharField(max_length=20, blank=True)
    submitted_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    grade = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True
    )
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-submitted_at',)
        constraints = [
            # Resubmitting replaces the earlier row -- one grade per student
            # per assignment, not a pile of attempts to sort through.
            models.UniqueConstraint(fields=('assignment', 'student'), name='unique_submission_per_student')
        ]

    def __str__(self):
        return f'{self.student_id} -> {self.assignment_id}'


class ClassQuiz(BaseModel):
    """A quiz the teacher writes for the class -- distinct from the
    learner-owned ``apps.quizzes.Quiz`` a student generates with فاحص."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        ARCHIVED = 'archived', 'Archived'

    classroom = models.ForeignKey('organizations.Classroom', on_delete=models.CASCADE, related_name='class_quizzes')
    subject = models.ForeignKey(
        'subjects.Subject', on_delete=models.SET_NULL, related_name='class_quizzes', null=True, blank=True
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    time_limit_minutes = models.PositiveIntegerField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True
    )

    class Meta:
        ordering = ('-created_at',)
        indexes = [models.Index(fields=('classroom', 'status'), name='classquiz_status_idx')]

    def __str__(self):
        return self.title


class ClassQuizQuestion(BaseModel):
    class QuestionType(models.TextChoices):
        MCQ = 'mcq', 'Multiple choice'
        TRUE_FALSE = 'true_false', 'True / false'
        WRITTEN = 'written', 'Written'

    class_quiz = models.ForeignKey(ClassQuiz, on_delete=models.CASCADE, related_name='questions')
    question_type = models.CharField(max_length=20, choices=QuestionType.choices, default=QuestionType.MCQ)
    text = models.TextField()
    explanation = models.TextField(blank=True)
    points = models.PositiveIntegerField(default=1)
    order = models.PositiveIntegerField()

    class Meta:
        ordering = ('order', 'id')
        constraints = [models.UniqueConstraint(fields=('class_quiz', 'order'), name='unique_classquiz_question_order')]

    def __str__(self):
        return self.text[:60]


class ClassQuizChoice(BaseModel):
    question = models.ForeignKey(ClassQuizQuestion, on_delete=models.CASCADE, related_name='choices')
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('order', 'id')

    def __str__(self):
        return self.text


class ClassQuizAttempt(SoftDeleteModel):
    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', 'In progress'
        SUBMITTED = 'submitted', 'Submitted'

    class_quiz = models.ForeignKey(ClassQuiz, on_delete=models.CASCADE, related_name='attempts')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='class_quiz_attempts')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    max_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # True once every written question has a grade -- a submitted attempt
    # with only mcq/true_false questions is fully graded immediately.
    fully_graded = models.BooleanField(default=False)

    class Meta:
        ordering = ('-started_at',)
        indexes = [models.Index(fields=('class_quiz', 'student', 'status'), name='classquiz_attempt_idx')]

    def __str__(self):
        return f'{self.student_id} -> {self.class_quiz_id}'


class ClassQuizAnswer(BaseModel):
    attempt = models.ForeignKey(ClassQuizAttempt, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(ClassQuizQuestion, on_delete=models.CASCADE, related_name='student_answers')
    selected_choice = models.ForeignKey(
        ClassQuizChoice, on_delete=models.SET_NULL, related_name='+', null=True, blank=True
    )
    text_answer = models.TextField(blank=True)
    # Null means "not graded yet" -- the written-answer case until a
    # teacher scores it. mcq/true_false are graded the moment they are
    # answered, so this is never null for them.
    is_correct = models.BooleanField(null=True, blank=True)
    points_awarded = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('attempt', 'question'), name='unique_classquiz_answer')]

    def __str__(self):
        return f'{self.attempt_id}:{self.question_id}'
