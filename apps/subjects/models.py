from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class EducationStage(BaseModel):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=1, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('order', 'id')
        verbose_name = 'Education Stage'
        verbose_name_plural = 'Education Stages'

    def __str__(self):
        return self.name


class Subject(BaseModel):
    name = models.CharField(max_length=120)
    education_stage = models.ForeignKey(
        EducationStage,
        on_delete=models.CASCADE,
        related_name='subjects',
    )
    grade_level = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('education_stage__order', 'name')
        verbose_name = 'Subject'
        verbose_name_plural = 'Subjects'
        constraints = [
            models.UniqueConstraint(
                fields=['name', 'education_stage', 'grade_level'],
                name='unique_subject_per_stage_and_grade',
            )
        ]

    def __str__(self):
        return f"{self.name} - {self.education_stage.name}"


class UserSubject(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='user_subjects',
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name='user_subjects',
    )

    class Meta:
        ordering = ('-created_at',)
        verbose_name = 'User Subject'
        verbose_name_plural = 'User Subjects'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'subject'],
                name='unique_user_subject',
            )
        ]

    def __str__(self):
        return f"{self.user.email} - {self.subject.name}"
