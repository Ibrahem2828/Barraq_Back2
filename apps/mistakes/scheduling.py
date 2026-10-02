"""A small Leitner ladder shared by mistake entries and flashcard reviews.

Not full SM-2: a fixed interval ladder is simpler to reason about and to
test, and is enough to turn "reviewed" into "reviewed again later rather
than tomorrow". ``REVIEW_RESULTS`` mirrors the vocabulary spaced-repetition
tools use (again/hard/good/easy) so a future client needs no translation.
"""

from datetime import timedelta

from django.utils import timezone

REVIEW_INTERVALS_DAYS = (1, 2, 4, 7, 14, 30, 60)
MAX_BOX = len(REVIEW_INTERVALS_DAYS) - 1
REVIEW_RESULTS = ('again', 'hard', 'good', 'easy')


def next_box(box: int, result: str) -> int:
    if result == 'again':
        return 0
    if result == 'hard':
        return max(0, box - 1)
    if result == 'good':
        return min(MAX_BOX, box + 1)
    if result == 'easy':
        return min(MAX_BOX, box + 2)
    raise ValueError(f'Unknown review result: {result}')


def schedule(entry, *, result: str, mastered_field: str | None = None):
    """Applies one review outcome in place; does not save()."""
    now = timezone.now()
    entry.box = next_box(entry.box, result)
    entry.next_review_at = now + timedelta(days=REVIEW_INTERVALS_DAYS[entry.box])
    entry.review_count += 1
    entry.last_reviewed_at = now
    if mastered_field and entry.box == MAX_BOX and result in ('good', 'easy'):
        entry.status = 'mastered'
        setattr(entry, mastered_field, now)
    return entry
