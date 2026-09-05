from celery import shared_task

from .models import StudentSource
from .services import process_source


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
    return process_source(source)
