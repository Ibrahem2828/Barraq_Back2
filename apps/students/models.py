from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common.models import BaseModel


class StudentProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='student_profile',
    )
    education_stage = models.ForeignKey(
        'subjects.EducationStage',
        on_delete=models.SET_NULL,
        related_name='student_profiles',
        null=True,
        blank=True,
    )
    grade_level = models.CharField(max_length=50, blank=True)
    specialization = models.CharField(max_length=100, blank=True)
    study_goal = models.TextField(blank=True)
    daily_study_hours = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(24)],
    )
    is_setup_completed = models.BooleanField(default=False)

    class Meta:
        ordering = ('-updated_at',)
        verbose_name = 'Student Profile'
        verbose_name_plural = 'Student Profiles'

    def __str__(self):
        return f"Profile - {self.user.email}"
