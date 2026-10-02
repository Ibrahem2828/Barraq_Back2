"""ساحة المعرفة (Knowledge Square): a structured community around lessons,
and صفي's "سؤال مرتبط بالدرس" -- the same mechanism at the same scope, so
one app serves both rather than two near-duplicates.

Every thread is classroom-scoped from day one, per the brief's own advice
("أطلقه داخل الصفوف أولاً"): `classroom` is required, never null, so there
is no platform-wide community to moderate yet.
"""

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel, SoftDeleteModel


class KnowledgeThread(SoftDeleteModel):
    class ThreadType(models.TextChoices):
        QUESTION = 'question', 'Question'
        EXPLANATION = 'explanation', 'Explanation'
        NOTE = 'note', 'Note'
        METHOD = 'method', 'Method'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        RESOLVED = 'resolved', 'Resolved'
        ARCHIVED = 'archived', 'Archived'

    classroom = models.ForeignKey('organizations.Classroom', on_delete=models.CASCADE, related_name='knowledge_threads')
    subject = models.ForeignKey(
        'subjects.Subject', on_delete=models.SET_NULL, related_name='knowledge_threads', null=True, blank=True
    )
    # Free text (e.g. "الوحدة الثانية"): the lesson/unit this is about, not
    # a modeled relation -- there is no shared "lesson" entity to point at.
    topic = models.CharField(max_length=255, blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='knowledge_threads')
    # True when the author held a staff grant over this classroom at
    # creation time -- a fact about who wrote it, fixed at posting, not
    # re-derived later (a supervisor's scope can change; the post's
    # provenance should not).
    is_teacher_content = models.BooleanField(default=False)
    thread_type = models.CharField(max_length=20, choices=ThreadType.choices, default=ThreadType.QUESTION)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    accepted_reply = models.ForeignKey(
        'KnowledgeReply', on_delete=models.SET_NULL, related_name='+', null=True, blank=True
    )

    class Meta:
        ordering = ('-created_at',)
        indexes = [models.Index(fields=('classroom', 'status', '-created_at'), name='thread_classroom_status_idx')]

    def __str__(self):
        return self.title


class KnowledgeReply(SoftDeleteModel):
    thread = models.ForeignKey(KnowledgeThread, on_delete=models.CASCADE, related_name='replies')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='knowledge_replies')
    is_teacher_reply = models.BooleanField(default=False)
    body = models.TextField()
    helpful_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('-helpful_count', 'created_at')

    def __str__(self):
        return self.body[:60]


class HelpfulVote(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    reply = models.ForeignKey(KnowledgeReply, on_delete=models.CASCADE, related_name='helpful_votes')

    class Meta:
        constraints = [models.UniqueConstraint(fields=('user', 'reply'), name='unique_helpful_vote')]


class SavedThread(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='saved_threads')
    thread = models.ForeignKey(KnowledgeThread, on_delete=models.CASCADE, related_name='saved_by')

    class Meta:
        ordering = ('-created_at',)
        constraints = [models.UniqueConstraint(fields=('user', 'thread'), name='unique_saved_thread')]
