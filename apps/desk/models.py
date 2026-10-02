"""My Desk (مكتبي): a personal study space the learner wants to come back to.

Everything here is owner-scoped only -- there is no admin or cross-user
surface, so none of it touches the tenant/RBAC boundary other apps enforce.
A desk is cheap: notes, a reading position, a focus timer and a short
tasklist, all plain storage with no AI call and no AI quota.
"""

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel, SoftDeleteModel


class DeskNote(SoftDeleteModel):
    """A note, highlight or bookmark the learner left on a source.

    ``anchor`` holds where it sits in the source (e.g. ``{"page": 3,
    "selected_text": "..."}`` ) -- opaque to the backend, read back verbatim
    by the reader that wrote it.
    """

    class NoteType(models.TextChoices):
        NOTE = 'note', 'Note'
        HIGHLIGHT = 'highlight', 'Highlight'
        BOOKMARK = 'bookmark', 'Bookmark'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='desk_notes')
    source = models.ForeignKey(
        'sources.StudentSource', on_delete=models.CASCADE, related_name='desk_notes', null=True, blank=True
    )
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL, related_name='desk_notes', null=True, blank=True
    )
    note_type = models.CharField(max_length=20, choices=NoteType.choices, default=NoteType.NOTE)
    body = models.TextField(blank=True)
    anchor = models.JSONField(default=dict, blank=True)
    color = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [models.Index(fields=('user', 'source', '-created_at'), name='desk_note_user_source_idx')]

    def __str__(self):
        return f'{self.get_note_type_display()} on {self.source_id} by {self.user_id}'


class ReadingPosition(BaseModel):
    """Where the learner left off in one source -- "أكمل من حيث توقفت"."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reading_positions')
    source = models.ForeignKey('sources.StudentSource', on_delete=models.CASCADE, related_name='reading_positions')
    position = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('user', 'source'), name='unique_reading_position_per_source')]

    def __str__(self):
        return f'{self.user_id} @ {self.source_id}'


class FocusSession(BaseModel):
    """A study timer tied to a subject or project -- "مؤقت تركيز"."""

    class Status(models.TextChoices):
        RUNNING = 'running', 'Running'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='focus_sessions')
    subject = models.ForeignKey(
        'subjects.Subject', on_delete=models.SET_NULL, related_name='focus_sessions', null=True, blank=True
    )
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL, related_name='focus_sessions', null=True, blank=True
    )
    task_label = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('-started_at',)
        indexes = [models.Index(fields=('user', 'status'), name='focus_session_user_status_idx')]

    def __str__(self):
        return f'{self.user_id} focus {self.started_at:%Y-%m-%d %H:%M}'


class SessionTask(BaseModel):
    """"ماذا سأنجز في هذه الجلسة؟" -- a short tasklist for today, or one session."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='session_tasks')
    focus_session = models.ForeignKey(
        FocusSession, on_delete=models.CASCADE, related_name='tasks', null=True, blank=True
    )
    # Tasks not tied to one timer still belong to a day's plan.
    task_date = models.DateField()
    title = models.CharField(max_length=255)
    is_done = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('task_date', 'order', 'id')
        indexes = [models.Index(fields=('user', 'task_date'), name='session_task_user_date_idx')]

    def __str__(self):
        return self.title


class DeskPreference(BaseModel):
    """Quiet personalization -- colors, background, favorite character."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='desk_preference')
    theme = models.CharField(max_length=30, blank=True)
    background = models.CharField(max_length=60, blank=True)
    accent_color = models.CharField(max_length=20, blank=True)
    favorite_character = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f'Desk preferences for {self.user_id}'
