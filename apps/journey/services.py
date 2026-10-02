from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    ChallengeProgress,
    PathStation,
    StationCompletion,
    StudentJourneyProgress,
    SubjectPath,
    Unlockable,
    UnlockedItem,
)


def total_gems(user) -> int:
    return StudentJourneyProgress.objects.filter(user=user).aggregate(total=Sum('gem_fragments_total'))['total'] or 0


def progress_for(user, subject):
    progress, _created = StudentJourneyProgress.objects.get_or_create(user=user, subject=subject)
    return progress


def _award_gems(user, subject, amount):
    progress = progress_for(user, subject)
    progress.gem_fragments_total += amount
    progress.save(update_fields=['gem_fragments_total', 'updated_at'])
    return progress


def _check_new_unlocks(user):
    """Call after any gem award: unlocks whatever the new total covers."""
    total = total_gems(user)
    already = set(UnlockedItem.objects.filter(user=user).values_list('unlockable_id', flat=True))
    newly_qualified = Unlockable.objects.filter(is_active=True, gem_requirement__lte=total).exclude(pk__in=already)
    UnlockedItem.objects.bulk_create([UnlockedItem(user=user, unlockable=item) for item in newly_qualified])


@transaction.atomic
def complete_station(user, station: PathStation):
    """Marks one station done, in order, and awards its gems once.

    Idempotent: completing an already-completed station is a no-op (no
    double award), so a client retry after a dropped response is safe.
    """
    if not station.is_active:
        raise ValidationError('This station is not open yet.')
    previous = PathStation.objects.filter(path_id=station.path_id, order__lt=station.order, is_active=True).order_by('-order').first()
    if previous and not StationCompletion.objects.filter(user=user, station=previous).exists():
        raise ValidationError('Complete the previous station first.')

    _created = StationCompletion.objects.get_or_create(user=user, station=station)[1]
    if not _created:
        return progress_for(user, station.path.subject)

    progress = _award_gems(user, station.path.subject, station.gem_reward)
    next_station = PathStation.objects.filter(path_id=station.path_id, order__gt=station.order, is_active=True).order_by('order').first()
    progress.current_station = next_station or station
    progress.save(update_fields=['current_station', 'updated_at'])
    _check_new_unlocks(user)
    return progress


def student_journey(user):
    """Every subject with an active path, with each station's state for
    this learner (locked / unlocked / completed)."""
    paths = SubjectPath.objects.filter(is_active=True).select_related('subject').prefetch_related('stations')
    completed_station_ids = set(StationCompletion.objects.filter(user=user).values_list('station_id', flat=True))
    progress_by_subject = {row.subject_id: row for row in StudentJourneyProgress.objects.filter(user=user)}

    rows = []
    for path in paths:
        stations = [station for station in path.stations.all() if station.is_active]
        unlocked_so_far = True
        station_rows = []
        for station in stations:
            completed = station.id in completed_station_ids
            station_rows.append({'station': station, 'completed': completed, 'unlocked': unlocked_so_far})
            if not completed:
                unlocked_so_far = False
        progress = progress_by_subject.get(path.subject_id)
        rows.append(
            {
                'subject': path.subject,
                'gem_fragments_total': progress.gem_fragments_total if progress else 0,
                'stations': station_rows,
            }
        )
    return rows


@transaction.atomic
def record_challenge_progress(user, challenge, *, increment=1):
    """The one entry point another app may call to move a challenge
    forward -- e.g. apps.mistakes marking one more mistake mastered. Caps
    at the target and awards gems exactly once, on completion."""
    row, _created = ChallengeProgress.objects.get_or_create(user=user, challenge=challenge)
    if row.is_completed:
        return row
    row.progress_value = min(challenge.target_value, row.progress_value + increment)
    if row.progress_value >= challenge.target_value:
        row.is_completed = True
        row.completed_at = timezone.now()
        row.save(update_fields=['progress_value', 'is_completed', 'completed_at', 'updated_at'])
        _award_gems(user, None, challenge.gem_reward)
        _check_new_unlocks(user)
        return row
    row.save(update_fields=['progress_value', 'updated_at'])
    return row
