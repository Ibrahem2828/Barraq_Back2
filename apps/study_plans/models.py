from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.common.models import BaseModel


class StudyPlan(BaseModel):
    class DifficultyLevel(models.TextChoices):
        EASY = 'easy', 'Easy'
        MEDIUM = 'medium', 'Medium'
        HARD = 'hard', 'Hard'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ACTIVE = 'active', 'Active'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    class GenerationType(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        AI = 'ai', 'AI'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='study_plans',
    )
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.SET_NULL,
        related_name='study_plans',
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    subject = models.ForeignKey(
        'subjects.Subject',
        on_delete=models.CASCADE,
        related_name='study_plans',
    )
    start_date = models.DateField()
    end_date = models.DateField()
    daily_study_minutes = models.PositiveIntegerField()
    goal = models.TextField(blank=True)
    difficulty_level = models.CharField(
        max_length=20,
        choices=DifficultyLevel.choices,
        default=DifficultyLevel.MEDIUM,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    generation_type = models.CharField(
        max_length=20,
        choices=GenerationType.choices,
        default=GenerationType.MANUAL,
    )
    ai_request_id = models.CharField(max_length=100, blank=True)
    # See apps.quizzes.models.Quiz.ai_job for why this is a real
    # database-enforced OneToOne rather than just a lookup string.
    ai_job = models.OneToOneField(
        "ai_integration.AIJob", on_delete=models.SET_NULL, null=True, blank=True, related_name="study_plan"
    )
    completion_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
    )

    class Meta:
        ordering = ('-created_at',)
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F('start_date')),
                name='study_plan_start_before_end',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'status'], name='study_plan_user_status_idx'),
            models.Index(fields=['project', 'status', '-created_at'], name='study_plan_project_status_idx'),
        ]

    def __str__(self):
        return f"{self.title} - {self.user.email}"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError(
                {'end_date': 'End date must be greater than or equal to start date.'}
            )


class StudyTask(BaseModel):
    class Priority(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED = 'completed', 'Completed'
        SKIPPED = 'skipped', 'Skipped'

    plan = models.ForeignKey(
        StudyPlan,
        on_delete=models.CASCADE,
        related_name='tasks',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    task_date = models.DateField()
    estimated_minutes = models.PositiveIntegerField()
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    order = models.PositiveIntegerField(default=1)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('task_date', 'order', 'id')
        constraints = [
            models.UniqueConstraint(
                fields=['plan', 'task_date', 'order'],
                name='unique_plan_task_order_per_day',
            )
        ]
        indexes = [
            models.Index(fields=['plan', 'status'], name='study_task_plan_status_idx'),
        ]

    def __str__(self):
        return f"{self.title} - {self.plan.title}"

    def clean(self):
        super().clean()
        if self.task_date and self.plan_id and (self.task_date < self.plan.start_date or self.task_date > self.plan.end_date):
            raise ValidationError(
                {'task_date': 'Task date must fall within the plan date range.'}
            )

    def save(self, *args, **kwargs):
        if self.status == self.Status.COMPLETED and self.completed_at is None:
            self.completed_at = timezone.now()
        elif self.status in {self.Status.PENDING, self.Status.IN_PROGRESS, self.Status.SKIPPED}:
            self.completed_at = None
        super().save(*args, **kwargs)


class StudyPlanProgressLog(models.Model):
    class Action(models.TextChoices):
        PLAN_CREATED = 'plan_created', 'Plan Created'
        TASK_COMPLETED = 'task_completed', 'Task Completed'
        TASK_SKIPPED = 'task_skipped', 'Task Skipped'
        TASK_REOPENED = 'task_reopened', 'Task Reopened'
        PLAN_COMPLETED = 'plan_completed', 'Plan Completed'
        PLAN_CANCELLED = 'plan_cancelled', 'Plan Cancelled'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='study_plan_progress_logs',
    )
    plan = models.ForeignKey(
        StudyPlan,
        on_delete=models.CASCADE,
        related_name='progress_logs',
    )
    task = models.ForeignKey(
        StudyTask,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='progress_logs',
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f"{self.action} - {self.plan.title}"
