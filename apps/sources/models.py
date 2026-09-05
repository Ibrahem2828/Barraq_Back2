from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel


def student_source_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    now = timezone.now()
    return (
        f'student_sources/{instance.user_id}/{now:%Y}/{now:%m}/'
        f'{uuid4().hex}{extension}'
    )


class StudentSource(BaseModel):
    class SourceType(models.TextChoices):
        PDF = 'pdf', 'PDF'
        TEXT = 'text', 'Text'
        IMAGE = 'image', 'Image'
        DOCUMENT = 'document', 'Document'
        PRESENTATION = 'presentation', 'Presentation'
        AUDIO = 'audio', 'Audio'
        LINK = 'link', 'Link'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        UPLOADED = 'uploaded', 'Uploaded'
        PROCESSING = 'processing', 'Processing'
        READY = 'ready', 'Ready'
        FAILED = 'failed', 'Failed'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='student_sources',
    )
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.SET_NULL,
        related_name='sources',
        null=True,
        blank=True,
    )
    subject = models.ForeignKey(
        'subjects.Subject',
        on_delete=models.SET_NULL,
        related_name='student_sources',
        null=True,
        blank=True,
    )
    collection = models.ForeignKey(
        'sources.StudentSourceCollection',
        on_delete=models.SET_NULL,
        related_name='sources',
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    source_type = models.CharField(
        max_length=20,
        choices=SourceType.choices,
        default=SourceType.OTHER,
    )
    file = models.FileField(upload_to=student_source_upload_to)
    original_filename = models.CharField(max_length=255)
    file_size = models.PositiveBigIntegerField(default=0)
    mime_type = models.CharField(max_length=120, blank=True)
    extension = models.CharField(max_length=20, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UPLOADED,
    )
    extracted_text = models.TextField(blank=True, null=True)
    processing_error = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = 'Student Source'
        verbose_name_plural = 'Student Sources'
        indexes = [
            models.Index(fields=['project', '-created_at'], name='source_project_timeline_idx'),
        ]

    def __str__(self):
        return f'{self.title} - {self.user.email}'


class StudentSourceCollection(BaseModel):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ARCHIVED = 'archived', 'Archived'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='student_source_collections',
    )
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.SET_NULL,
        related_name='source_collections',
        null=True,
        blank=True,
    )
    subject = models.ForeignKey(
        'subjects.Subject',
        on_delete=models.SET_NULL,
        related_name='student_source_collections',
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=40, blank=True)
    icon = models.CharField(max_length=80, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    class Meta:
        ordering = ('-updated_at', '-created_at')
        verbose_name = 'Student Source Collection'
        verbose_name_plural = 'Student Source Collections'
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['project', 'status'], name='source_collection_project_idx'),
        ]

    def __str__(self):
        return f'{self.name} - {self.user.email}'

    @property
    def source_count(self):
        return self.sources.count()

    @property
    def last_source_at(self):
        last_source = self.sources.order_by('-created_at').only('created_at').first()
        return last_source.created_at if last_source else None

    @property
    def total_file_size(self):
        return self.sources.aggregate(total=models.Sum('file_size'))['total'] or 0


class StudentSourceInteraction(BaseModel):
    class Character(models.TextChoices):
        KHOTA = 'khota', 'Khota'
        FAHES = 'fahes', 'Fahes'
        RASHEED = 'rasheed', 'Rasheed'
        KHOLASA = 'kholasa', 'Kholasa'
        SADA = 'sada', 'Sada'

    class Action(models.TextChoices):
        CREATE_STUDY_PLAN = 'create_study_plan', 'Create Study Plan'
        CREATE_QUIZ = 'create_quiz', 'Create Quiz'
        STUDY_ADVICE = 'study_advice', 'Study Advice'
        SUMMARIZE = 'summarize', 'Summarize'
        VOICE_HELP = 'voice_help', 'Voice Help'
        UNAVAILABLE = 'unavailable', 'Unavailable'

    class Status(models.TextChoices):
        CREATED = 'created', 'Created'
        COMPLETED = 'completed', 'Completed'
        UNAVAILABLE = 'unavailable', 'Unavailable'
        FAILED = 'failed', 'Failed'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='student_source_interactions',
    )
    source = models.ForeignKey(
        StudentSource,
        on_delete=models.CASCADE,
        related_name='interactions',
        null=True,
        blank=True,
    )
    collection = models.ForeignKey(
        StudentSourceCollection,
        on_delete=models.CASCADE,
        related_name='interactions',
        null=True,
        blank=True,
    )
    character = models.CharField(max_length=20, choices=Character.choices)
    action = models.CharField(max_length=30, choices=Action.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATED,
    )
    result_type = models.CharField(max_length=50, blank=True)
    result_id = models.PositiveIntegerField(null=True, blank=True)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = 'Student Source Interaction'
        verbose_name_plural = 'Student Source Interactions'

    def __str__(self):
        target = self.source or self.collection
        return f'{self.character} - {target}'

    def clean(self):
        super().clean()
        if bool(self.source_id) == bool(self.collection_id):
            raise ValidationError(
                {'source': 'Interaction must target exactly one source or collection.'}
            )
        if self.source_id and self.user_id and self.source.user_id != self.user_id:
            raise ValidationError({'user': 'Interaction user must own the source.'})
        if (
            self.collection_id
            and self.user_id
            and self.collection.user_id != self.user_id
        ):
            raise ValidationError({'user': 'Interaction user must own the collection.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
