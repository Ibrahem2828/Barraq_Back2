from django.utils import timezone

from .models import FocusSession, ReadingPosition, SessionTask


def continue_reading(user):
    """The one source the learner was last in, for "أكمل من حيث توقفت"."""
    return (
        ReadingPosition.objects.filter(user=user, source__isnull=False)
        .select_related('source', 'source__project')
        .order_by('-updated_at')
        .first()
    )


def today_tasks(user):
    return SessionTask.objects.filter(user=user, task_date=timezone.localdate()).order_by('order', 'id')


def running_focus_session(user):
    return FocusSession.objects.filter(user=user, status=FocusSession.Status.RUNNING).order_by('-started_at').first()


def stop_focus_session(session, *, status):
    """Ends a running session and records its actual duration."""
    now = timezone.now()
    session.status = status
    session.ended_at = now
    session.duration_seconds = max(0, int((now - session.started_at).total_seconds()))
    session.save(update_fields=['status', 'ended_at', 'duration_seconds', 'updated_at'])
    return session
