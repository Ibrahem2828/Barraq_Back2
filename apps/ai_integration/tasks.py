from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .client import AIServiceClient, AIServiceError
from .error_codes import ErrorCode
from .models import AIFeedback, AIJob, AIJobDispatchOutbox
from .services import TERMINAL_JOB_STATUSES, fail_job, forward_feedback, submit_job_to_service


def _next_retry_delay(attempt_count):
    base = settings.AI_DISPATCH_RETRY_BASE_SECONDS
    return min(base * (2 ** max(0, attempt_count - 1)), 900)


def _claim_outbox_row(job_id):
    """Claim this job's outbox row for dispatch, including recovery of a row
    a crashed worker left stuck in DISPATCHING (spec failure-injection:
    'Worker dies after provider response and before persist')."""

    stale_lock_cutoff = timezone.now() - timedelta(seconds=settings.AI_DISPATCH_LOCK_TIMEOUT_SECONDS)
    return (
        AIJobDispatchOutbox.objects.select_for_update(skip_locked=True)
        .filter(
            Q(status__in=[AIJobDispatchOutbox.Status.PENDING, AIJobDispatchOutbox.Status.FAILED])
            | Q(status=AIJobDispatchOutbox.Status.DISPATCHING, locked_at__lte=stale_lock_cutoff)
        )
        .filter(job_id=job_id)
        .select_related("job")
        .first()
    )


@shared_task(bind=True, autoretry_for=(), max_retries=3, retry_backoff=True, retry_jitter=True, name="ai_integration.dispatch_job")
def dispatch_ai_job(self, job_id):
    with transaction.atomic():
        outbox = _claim_outbox_row(job_id)
        if outbox is None:
            # Already claimed by another worker, already dispatched, or the
            # job reached a terminal state before this attempt ran.
            return None
        outbox.status = AIJobDispatchOutbox.Status.DISPATCHING
        outbox.attempt_count += 1
        outbox.locked_at = timezone.now()
        outbox.save(update_fields=["status", "attempt_count", "locked_at", "updated_at"])
        job = outbox.job

    if job.status not in {AIJob.Status.QUEUED, AIJob.Status.CREATED}:
        outbox.status = AIJobDispatchOutbox.Status.DISPATCHED
        outbox.save(update_fields=["status", "updated_at"])
        return str(job.public_id)

    try:
        submit_job_to_service(job)
    except Exception as exc:  # noqa: BLE001 -- any dispatch failure must retry/outbox-fail, not crash the worker
        outbox.status = AIJobDispatchOutbox.Status.FAILED
        outbox.last_error = str(exc)[:2000]
        outbox.next_retry_at = timezone.now() + timedelta(seconds=_next_retry_delay(outbox.attempt_count))
        outbox.save(update_fields=["status", "last_error", "next_retry_at", "updated_at"])
        if getattr(exc, "retryable", False) and self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        if not getattr(exc, "retryable", False):
            # A content-version mismatch, invalid scope, unsupported input,
            # or another permanent rejection cannot improve on the next Beat
            # sweep. Mark the job terminal now so reconciliation never turns
            # a deterministic failure into a retry storm.
            fail_job(job, exc)
            return str(job.public_id)
        # Celery's own retries are exhausted, but the outbox row survives:
        # the Beat sweep (reconcile_stuck_ai_jobs) will keep re-dispatching
        # it up to AI_DISPATCH_MAX_ATTEMPTS before giving up for good.
        raise

    outbox.status = AIJobDispatchOutbox.Status.DISPATCHED
    outbox.dispatched_at = timezone.now()
    outbox.save(update_fields=["status", "dispatched_at", "updated_at"])
    return str(job.public_id)


@shared_task(name="ai_integration.reconcile_stuck_jobs")
def reconcile_stuck_ai_jobs():
    """Beat-driven safety net (spec: 'Accepted job loss: 0'). Independent of
    Celery's own per-task retry, so a broker/worker gap between job creation
    and task pickup -- not just an AI-service outage -- still recovers."""

    now = timezone.now()
    stale_lock_cutoff = now - timedelta(seconds=settings.AI_DISPATCH_LOCK_TIMEOUT_SECONDS)
    candidates = (
        AIJobDispatchOutbox.objects.select_related("job")
        .exclude(job__status__in=TERMINAL_JOB_STATUSES)
        .filter(
            Q(status__in=[AIJobDispatchOutbox.Status.PENDING, AIJobDispatchOutbox.Status.FAILED], next_retry_at__lte=now)
            | Q(status=AIJobDispatchOutbox.Status.DISPATCHING, locked_at__lte=stale_lock_cutoff)
        )
        .order_by("updated_at")[:200]
    )
    redispatched = 0
    gave_up = 0
    for outbox in candidates:
        if outbox.attempt_count >= settings.AI_DISPATCH_MAX_ATTEMPTS:
            fail_job(
                outbox.job,
                AIServiceError(
                    "AI job exceeded the maximum dispatch attempts without reaching the AI service.",
                    code=ErrorCode.PROVIDER_UNAVAILABLE,
                    retryable=False,
                ),
            )
            outbox.status = AIJobDispatchOutbox.Status.FAILED
            outbox.save(update_fields=["status", "updated_at"])
            gave_up += 1
            continue
        dispatch_ai_job.delay(outbox.job_id)
        redispatched += 1
    return {"redispatched": redispatched, "gave_up": gave_up}


@shared_task(bind=True, max_retries=3, retry_backoff=True, retry_jitter=True, name="ai_integration.forward_feedback")
def forward_ai_feedback(self, feedback_id):
    feedback = AIFeedback.objects.select_related("job").get(pk=feedback_id)
    if feedback.forwarded_to_ai_service:
        return feedback.id
    try:
        forward_feedback(feedback)
    except Exception as exc:  # noqa: BLE001 -- any forwarding failure should retry, not crash the worker
        raise self.retry(exc=exc) from exc
    return feedback.id


@shared_task(bind=True, max_retries=3, retry_backoff=True, retry_jitter=True, name="ai_integration.cancel_external_job")
def cancel_external_ai_job(self, external_job_id, user_id):
    try:
        AIServiceClient().cancel_job(external_job_id, user_id=user_id)
    except AIServiceError as exc:
        if exc.retryable:
            raise self.retry(exc=exc) from exc
        return False
    return True
