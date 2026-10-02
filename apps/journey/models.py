"""رحلة برّاق: reward completion and improvement, never "kept the app open".

Progression is strictly sequential by station order within one subject's
path -- no speculative unlock-requirement language to design, test and get
wrong; a station unlocks once the previous one in the same path is done.
Gems are per subject (`StudentJourneyProgress`, one row per user+subject);
an unlockable's `gem_requirement` is checked against the learner's total
across every subject, since a desk theme is not tied to one class.
"""

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class SubjectPath(BaseModel):
    """One path per subject -- a sequence of stations a learner walks."""

    subject = models.OneToOneField('subjects.Subject', on_delete=models.CASCADE, related_name='journey_path')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'Path for {self.subject_id}'


class PathStation(BaseModel):
    path = models.ForeignKey(SubjectPath, on_delete=models.CASCADE, related_name='stations')
    title = models.CharField(max_length=255)
    order = models.PositiveIntegerField()
    gem_reward = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('order', 'id')
        constraints = [models.UniqueConstraint(fields=('path', 'order'), name='unique_station_order')]

    def __str__(self):
        return self.title


class StudentJourneyProgress(BaseModel):
    """One row per (learner, subject): the learner's gems and current
    station on that subject's path. `subject=None` is the one "general"
    row per learner -- gems from a weekly challenge, which belongs to no
    single subject."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='journey_progress')
    subject = models.ForeignKey(
        'subjects.Subject', on_delete=models.CASCADE, related_name='+', null=True, blank=True
    )
    gem_fragments_total = models.PositiveIntegerField(default=0)
    current_station = models.ForeignKey(PathStation, on_delete=models.SET_NULL, related_name='+', null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'subject'), condition=models.Q(subject__isnull=False), name='unique_progress_per_subject'
            ),
            models.UniqueConstraint(
                fields=('user',), condition=models.Q(subject__isnull=True), name='unique_general_progress'
            ),
        ]

    def __str__(self):
        return f'{self.user_id} @ {self.subject_id}: {self.gem_fragments_total} gems'


class StationCompletion(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='station_completions')
    station = models.ForeignKey(PathStation, on_delete=models.CASCADE, related_name='completions')

    class Meta:
        constraints = [models.UniqueConstraint(fields=('user', 'station'), name='unique_station_completion')]

    def __str__(self):
        return f'{self.user_id} completed {self.station_id}'


class Unlockable(BaseModel):
    class Category(models.TextChoices):
        DESK_THEME = 'desk_theme', 'Desk theme'
        DESK_BACKGROUND = 'desk_background', 'Desk background'
        CHARACTER_OUTFIT = 'character_outfit', 'Character outfit'

    code = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=30, choices=Category.choices)
    gem_requirement = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class UnlockedItem(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='unlocked_items')
    unlockable = models.ForeignKey(Unlockable, on_delete=models.CASCADE, related_name='unlocked_by')

    class Meta:
        constraints = [models.UniqueConstraint(fields=('user', 'unlockable'), name='unique_unlocked_item')]


class WeeklyChallenge(BaseModel):
    """A manually set, time-boxed target -- "عالج خمسة أخطاء" -- whose
    progress is reported by whatever feature the challenge names, not
    computed here (`apps.journey.services.record_challenge_progress` is
    the single place another app may safely call into this one)."""

    code = models.SlugField(max_length=60)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    week_start = models.DateField()
    target_value = models.PositiveIntegerField(default=1)
    gem_reward = models.PositiveIntegerField(default=20)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('-week_start',)
        constraints = [models.UniqueConstraint(fields=('code', 'week_start'), name='unique_challenge_per_week')]

    def __str__(self):
        return f'{self.title} ({self.week_start})'


class ChallengeProgress(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='challenge_progress')
    challenge = models.ForeignKey(WeeklyChallenge, on_delete=models.CASCADE, related_name='progress_rows')
    progress_value = models.PositiveIntegerField(default=0)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('user', 'challenge'), name='unique_challenge_progress')]


class ClassGoal(BaseModel):
    """A shared target for one class -- "إكمال مراجعة وحدة معًا". Progress
    is set by the teacher; this is intentionally not an automatic rollup of
    student activity, which would need a cross-domain aggregation this
    round does not build."""

    classroom = models.ForeignKey('organizations.Classroom', on_delete=models.CASCADE, related_name='journey_goals')
    title = models.CharField(max_length=255)
    target_value = models.PositiveIntegerField(default=1)
    progress_value = models.PositiveIntegerField(default=0)
    is_completed = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='+', null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return self.title
