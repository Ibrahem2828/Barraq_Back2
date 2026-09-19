from celery import shared_task

from .models import StudentSource
from .services import mark_source_failed, process_source


@shared_task(
    bind=True,
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    name="sources.process_source",
)
def process_source_task(self, source_id: int):
    source = StudentSource.objects.filter(pk=source_id).first()
    if source is None:
        return {"success": False, "message": "Source no longer exists."}
    if source.status == StudentSource.Status.READY:
        return {"success": True, "message": "Source is already ready."}
    try:
        return process_source(source)
    except OSError:
        # autoretry_for handles the retry itself; this only records the
        # terminal state on the last attempt, since process_source's
        # transaction rolls back every time it re-raises.
        if self.request.retries >= self.max_retries:
            mark_source_failed(source, "Source storage was unavailable.")
        raise
