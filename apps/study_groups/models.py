"""رفاق برّاق (Study Buddies): small, invite-only study groups.

Deliberately not built in this round, per the feature brief itself: private
1:1 messages between strangers, audio/video sessions, and public discovery
(a group is reachable only by its invite code, never listed). A "session"
here is one shared, server-computed countdown -- no realtime transport is
needed for that.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel
from apps.organizations.models import generate_join_code


class StudyGroup(BaseModel):
    name = models.CharField(max_length=255)
    invite_code = models.CharField(max_length=8, unique=True, default=generate_join_code, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='owned_study_groups')
    max_members = models.PositiveSmallIntegerField(default=8)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return self.name


class GroupMembership(BaseModel):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        MEMBER = 'member', 'Member'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        LEFT = 'left', 'Left'
        BANNED = 'banned', 'Banned'

    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='study_group_memberships')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    joined_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('group', 'user'), condition=models.Q(status='active'), name='unique_active_group_membership'
            )
        ]
        indexes = [models.Index(fields=('group', 'status'), name='group_membership_status_idx')]

    def __str__(self):
        return f'{self.user_id} in {self.group_id} ({self.status})'


class GroupBan(BaseModel):
    """Prevents rejoining by invite code once banned -- separate from
    GroupMembership.Status.BANNED so a ban outlives the membership row it
    came from and a re-used invite code cannot quietly let someone back in.
    """

    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='bans')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    banned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('group', 'user'), name='unique_group_ban')]


class GroupGoal(BaseModel):
    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='goals')
    title = models.CharField(max_length=255)
    target_date = models.DateField(null=True, blank=True)
    is_done = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True)

    class Meta:
        ordering = ('is_done', 'target_date', '-created_at')

    def __str__(self):
        return self.title


class GroupTask(BaseModel):
    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=255)
    is_done = models.BooleanField(default=False)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True)
    order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True)

    class Meta:
        ordering = ('order', 'id')

    def __str__(self):
        return self.title


class GroupSession(BaseModel):
    """A shared, synced study timer: every member reads the same
    server-computed remaining time from one `started_at` + `duration_minutes`
    -- no realtime channel needed for "synced"."""

    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        ACTIVE = 'active', 'Active'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='sessions')
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True)

    class Meta:
        ordering = ('-scheduled_at',)

    def __str__(self):
        return f'{self.group_id} @ {self.scheduled_at:%Y-%m-%d %H:%M}'


class GroupSessionSummary(BaseModel):
    session = models.OneToOneField(GroupSession, on_delete=models.CASCADE, related_name='summary')
    tasks_completed_count = models.PositiveIntegerField(default=0)
    duration_actual_seconds = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f'Summary for {self.session_id}'


class GroupMessage(BaseModel):
    """A message's file is an existing *owned* source (see
    `attached_source`), never a fresh upload -- a group shares what a
    member already has, the same "copy, don't re-upload" rule as the
    Classroom Shared Library."""

    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='messages')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='group_messages')
    body = models.TextField(blank=True)
    attached_source = models.ForeignKey(
        'sources.StudentSource', on_delete=models.SET_NULL, related_name='+', null=True, blank=True
    )
    is_flagged = models.BooleanField(default=False)

    class Meta:
        ordering = ('created_at',)
        indexes = [models.Index(fields=('group', 'created_at'), name='group_message_group_time_idx')]

    def __str__(self):
        return f'{self.user_id}: {self.body[:40]}'


class MessageReport(BaseModel):
    message = models.ForeignKey(GroupMessage, on_delete=models.CASCADE, related_name='reports')
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('message', 'reported_by'), name='unique_message_report')]
