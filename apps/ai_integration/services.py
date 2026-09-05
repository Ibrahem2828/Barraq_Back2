from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Avg, Count
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.quizzes.models import AttemptStatusChoices, QuizAttempt
from apps.sources.models import StudentSource, StudentSourceInteraction
from apps.subscriptions.services import (
    commit_character_request,
    refund_character_request,
    reserve_character_request,
)

from .client import AIServiceClient, AIServiceError
from .error_codes import ErrorCode
from .materializers import materialize_job
from .models import AIJob, AIJobDispatchOutbox

logger = logging.getLogger(__name__)

TASK_CHARACTER = {
    AIJob.TaskType.FAHES_GENERATE_QUIZ: AIJob.Character.FAHES,
    AIJob.TaskType.KHOTA_GENERATE_PLAN: AIJob.Character.KHOTA,
    AIJob.TaskType.RASHEED_RECOMMENDATIONS: AIJob.Character.RASHEED,
    AIJob.TaskType.KHOLASA_GENERATE_SUMMARY: AIJob.Character.KHOLASA,
    AIJob.TaskType.SADA_TRANSCRIBE_AUDIO: AIJob.Character.SADA,
}

TERMINAL_JOB_STATUSES = {AIJob.Status.COMPLETED, AIJob.Status.FAILED, AIJob.Status.CANCELED}
JOB_PROGRESS_TRANSITIONS = {
    AIJob.Status.CREATED: {AIJob.Status.QUEUED, AIJob.Status.FAILED, AIJob.Status.CANCELED},
    AIJob.Status.QUEUED: {AIJob.Status.SUBMITTED, AIJob.Status.PROCESSING, AIJob.Status.FAILED, AIJob.Status.CANCELED},
    AIJob.Status.SUBMITTED: {AIJob.Status.PROCESSING, AIJob.Status.VALIDATING, AIJob.Status.FAILED, AIJob.Status.CANCELED},
    AIJob.Status.PROCESSING: {AIJob.Status.VALIDATING, AIJob.Status.OUTPUT_READY, AIJob.Status.FAILED, AIJob.Status.CANCELED},
    AIJob.Status.VALIDATING: {AIJob.Status.OUTPUT_READY, AIJob.Status.FAILED, AIJob.Status.CANCELED},
    AIJob.Status.OUTPUT_READY: {AIJob.Status.MATERIALIZING, AIJob.Status.FAILED, AIJob.Status.CANCELED},
    AIJob.Status.MATERIALIZING: {AIJob.Status.COMPLETED, AIJob.Status.FAILED, AIJob.Status.CANCELED},
}

_ALLOWED_LANGUAGES = {"ar", "en"}
_ALLOWED_MODEL_TIERS = {"fast", "balanced", "high_quality"}


def _as_mapping(value, field_name):
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ValidationError({field_name: "A JSON object is required."})
    return value


def _optional_text(value, field_name, *, max_length):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value.strip()) > max_length:
        raise ValidationError({field_name: f"Must be text up to {max_length} characters."})
    return value.strip()


def _language(payload):
    language = payload.get("language", "ar")
    if language not in _ALLOWED_LANGUAGES:
        raise ValidationError({"input.language": "Supported languages are ar and en."})
    return language


def _string_list(value, field_name, *, max_length, allowed_values=None):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > max_length or any(not isinstance(item, str) for item in value):
        raise ValidationError({field_name: f"Must be a list of at most {max_length} text values."})
    normalized = [item.strip() for item in value]
    if any(not item for item in normalized) or (allowed_values and any(item not in allowed_values for item in normalized)):
        raise ValidationError({field_name: "Contains an unsupported value."})
    return normalized


def _validated_iso_dates(values, field_name):
    for value in values:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError({field_name: "Dates must use ISO-8601 format."}) from exc
    return values


def _integer(value, field_name, *, default, minimum, maximum):
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValidationError({field_name: "Must be an integer."})
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Must be an integer."}) from exc
    if parsed < minimum or parsed > maximum:
        raise ValidationError({field_name: f"Must be between {minimum} and {maximum}."})
    return parsed


def _boolean(value, field_name, *, default):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValidationError({field_name: "Must be boolean."})
    return value


def _source_ids(*, source=None, collection=None):
    if source is not None:
        return [str(source.id)]
    if collection is None:
        return []
    ids = list(
        collection.sources.filter(
            status__in=[StudentSource.Status.UPLOADED, StudentSource.Status.READY]
        )
        .order_by("id")
        .values_list("id", flat=True)[:10]
    )
    if not ids:
        raise ValidationError({"source": "At least one usable source is required."})
    return [str(source_id) for source_id in ids]


def build_fahes_job_input(*, source=None, collection=None, subject=None, input_payload=None, parameters=None):
    payload = _as_mapping(input_payload, "input")
    params = _as_mapping(parameters, "parameters")
    input_data = {
        "source_ids": _source_ids(source=source, collection=collection),
        "question_count": _integer(
            payload.get("question_count", params.get("question_count", params.get("questions_count"))),
            "input.question_count",
            default=10,
            minimum=3,
            maximum=50,
        ),
        "question_types": _string_list(
            payload.get("question_types"),
            "input.question_types",
            max_length=2,
            allowed_values={"mcq", "true_false"},
        )
        or ["mcq", "true_false"],
        "language": _language(payload),
    }
    if subject is not None:
        input_data["subject_id"] = str(subject.id)
    for field_name, max_length in (("topic", 300), ("instructions", 1000)):
        value = _optional_text(payload.get(field_name, params.get(field_name)), f"input.{field_name}", max_length=max_length)
        if value is not None:
            input_data[field_name] = value
    difficulty = payload.get("difficulty", params.get("difficulty"))
    if difficulty is not None:
        if difficulty not in {"easy", "medium", "hard"}:
            raise ValidationError({"input.difficulty": "Unsupported difficulty."})
        input_data["difficulty"] = difficulty
    return input_data


def build_kholasa_job_input(*, source=None, collection=None, input_payload=None, parameters=None):
    payload = _as_mapping(input_payload, "input")
    params = _as_mapping(parameters, "parameters")
    summary_length = payload.get("summary_length", params.get("summary_length", "medium"))
    if summary_length not in {"short", "medium", "detailed"}:
        raise ValidationError({"input.summary_length": "Unsupported summary length."})
    input_data = {
        "source_ids": _source_ids(source=source, collection=collection),
        "summary_length": summary_length,
        "focus_topics": _string_list(payload.get("focus_topics"), "input.focus_topics", max_length=30),
        "include_review_questions": _boolean(
            payload.get("include_review_questions"), "input.include_review_questions", default=True
        ),
        "include_flashcards": _boolean(
            payload.get("include_flashcards"), "input.include_flashcards", default=True
        ),
        "language": _language(payload),
    }
    instructions = _optional_text(payload.get("instructions"), "input.instructions", max_length=1000)
    if instructions is not None:
        input_data["instructions"] = instructions
    return input_data


def _latest_weak_topics(*, user, project=None, limit=50):
    """Pull weak topics from the learner's own latest Rasheed recommendation
    instead of requiring the client to copy them by hand (spec section 19:
    Learning Loop, رشيد -> خطى)."""

    from apps.analytics.models import StudentRecommendation

    queryset = StudentRecommendation.objects.filter(user=user)
    if project is not None:
        queryset = queryset.filter(ai_job__project=project)
    latest = queryset.order_by("-created_at").first()
    if latest is None:
        return []
    weaknesses = latest.weaknesses if isinstance(latest.weaknesses, list) else []
    return [str(item).strip() for item in weaknesses if isinstance(item, str) and item.strip()][:limit]


def build_khota_job_input(
    *, source=None, collection=None, subject=None, input_payload=None, parameters=None, user=None, project=None
):
    payload = _as_mapping(input_payload, "input")
    params = _as_mapping(parameters, "parameters")
    start_date = payload.get("start_date", params.get("start_date")) or timezone.localdate().isoformat()
    end_date = payload.get("end_date", params.get("end_date"))
    try:
        start = date.fromisoformat(str(start_date))
        end = date.fromisoformat(str(end_date)) if end_date else start + timedelta(days=6)
    except ValueError as exc:
        raise ValidationError({"input": "start_date and end_date must be ISO dates."}) from exc
    if end < start or (end - start).days > 180:
        raise ValidationError({"input.end_date": "Study plan dates must span from 0 to 180 days."})
    supplied_subject_ids = _string_list(payload.get("subject_ids"), "input.subject_ids", max_length=20)
    subject_ids = [str(subject.id)] if subject is not None else supplied_subject_ids
    if not subject_ids:
        raise ValidationError({"subject": "A subject is required for a study plan."})
    exam_dates = _as_mapping(payload.get("exam_dates"), "input.exam_dates")
    for subject_id, date_value in exam_dates.items():
        try:
            date.fromisoformat(str(date_value))
        except ValueError as exc:
            raise ValidationError({"input.exam_dates": "Exam dates must be ISO dates."}) from exc
        if not isinstance(subject_id, str):
            raise ValidationError({"input.exam_dates": "Subject ids must be text."})
    return {
        "source_ids": _source_ids(source=source, collection=collection),
        "subject_ids": subject_ids,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily_available_minutes": _integer(
            payload.get("daily_available_minutes", params.get("daily_available_minutes", params.get("daily_minutes"))),
            "input.daily_available_minutes",
            default=60,
            minimum=20,
            maximum=720,
        ),
        "exam_dates": exam_dates,
        "weak_topics": _string_list(payload.get("weak_topics"), "input.weak_topics", max_length=50)
        or (_latest_weak_topics(user=user, project=project) if user is not None else []),
        "excluded_dates": _validated_iso_dates(
            _string_list(payload.get("excluded_dates"), "input.excluded_dates", max_length=60),
            "input.excluded_dates",
        ),
        "preferred_session_minutes": _integer(
            payload.get("preferred_session_minutes", params.get("preferred_session_minutes")),
            "input.preferred_session_minutes",
            default=45,
            minimum=15,
            maximum=180,
        ),
        "language": _language(payload),
    }


def _topic_performance(user, *, limit=20):
    """Real per-quiz-topic accuracy from submitted attempts. ``Quiz.topic``
    is a free-text label; the subject name is the fallback grouping when a
    quiz was never given one -- there is no finer-grained topic taxonomy in
    this schema today, so this is the most granular *authoritative* signal
    available (spec: Rasheed never invents numbers)."""

    rows = (
        QuizAttempt.objects.filter(user=user, status=AttemptStatusChoices.SUBMITTED)
        .values("quiz__topic", "quiz__subject__name")
        .annotate(average_percentage=Avg("percentage"), answered=Count("id"))
        .order_by("average_percentage")[:limit]
    )
    performance = []
    for row in rows:
        label = (row["quiz__topic"] or row["quiz__subject__name"] or "").strip()
        if not label:
            continue
        performance.append(
            {
                "topic": label[:300],
                "score": float(row["average_percentage"] or 0),
                "answered_questions": int(row["answered"] or 0),
            }
        )
    return performance


def _recent_quiz_actions(user, *, limit=10):
    attempts = (
        QuizAttempt.objects.filter(user=user, status=AttemptStatusChoices.SUBMITTED)
        .select_related("quiz")
        .order_by("-submitted_at")[:limit]
    )
    return [
        {
            "action": "quiz_attempt",
            "quiz_title": attempt.quiz.title[:200],
            "percentage": float(attempt.percentage),
            "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        }
        for attempt in attempts
    ]


def build_rasheed_job_input(*, user, input_payload=None, parameters=None):
    payload = _as_mapping(input_payload, "input")
    _as_mapping(parameters, "parameters")
    metrics = QuizAttempt.objects.filter(user=user, status=AttemptStatusChoices.SUBMITTED).aggregate(
        attempts=Count("id"), average_percentage=Avg("percentage")
    )
    input_data = {
        "metrics": [
            {
                "name": "quiz_attempts",
                "value": float(metrics["attempts"] or 0),
                "unit": "attempts",
                "period": "all_time",
                "authoritative": True,
            },
            {
                "name": "average_quiz_percentage",
                "value": float(metrics["average_percentage"] or 0),
                "unit": "percent",
                "period": "all_time",
                "authoritative": True,
            },
        ],
        "topic_performance": _topic_performance(user),
        "recent_actions": _recent_quiz_actions(user),
        "language": _language(payload),
    }
    learner_goal = _optional_text(payload.get("learner_goal"), "input.learner_goal", max_length=500)
    if learner_goal is not None:
        input_data["learner_goal"] = learner_goal
    return input_data


def build_sada_job_input(*, source=None, input_payload=None, parameters=None):
    payload = _as_mapping(input_payload, "input")
    _as_mapping(parameters, "parameters")
    if source is None or source.source_type != StudentSource.SourceType.AUDIO:
        raise ValidationError({"source": "Sada requires one owned audio source."})
    cleanup_level = payload.get("cleanup_level", "educational")
    if cleanup_level not in {"literal", "light", "educational"}:
        raise ValidationError({"input.cleanup_level": "Unsupported cleanup level."})
    language = payload.get("language", "ar")
    if not isinstance(language, str) or not language.isalpha() or not 2 <= len(language) <= 5:
        raise ValidationError({"input.language": "Must be a supported language code."})
    return {
        "source_id": str(source.id),
        "language": language,
        "diarize": _boolean(payload.get("diarize"), "input.diarize", default=False),
        "known_terms": _string_list(payload.get("known_terms"), "input.known_terms", max_length=200),
        "cleanup_level": cleanup_level,
    }


def build_task_input(
    *, user, task_type, source=None, collection=None, subject=None, project=None, input_payload=None, parameters=None
):
    """Build a strict Baraq AI V2 task input from Django-authoritative data."""

    builders = {
        AIJob.TaskType.FAHES_GENERATE_QUIZ: lambda: build_fahes_job_input(
            source=source, collection=collection, subject=subject, input_payload=input_payload, parameters=parameters
        ),
        AIJob.TaskType.KHOLASA_GENERATE_SUMMARY: lambda: build_kholasa_job_input(
            source=source, collection=collection, input_payload=input_payload, parameters=parameters
        ),
        AIJob.TaskType.KHOTA_GENERATE_PLAN: lambda: build_khota_job_input(
            source=source,
            collection=collection,
            subject=subject,
            input_payload=input_payload,
            parameters=parameters,
            user=user,
            project=project,
        ),
        AIJob.TaskType.RASHEED_RECOMMENDATIONS: lambda: build_rasheed_job_input(
            user=user, input_payload=input_payload, parameters=parameters
        ),
        AIJob.TaskType.SADA_TRANSCRIBE_AUDIO: lambda: build_sada_job_input(
            source=source, input_payload=input_payload, parameters=parameters
        ),
    }
    try:
        return builders[task_type]()
    except KeyError as exc:
        raise ValidationError({"task_type": "Unsupported AI task."}) from exc


def build_model_policy(parameters):
    params = _as_mapping(parameters, "parameters")
    policy = _as_mapping(params.get("model_policy"), "parameters.model_policy")
    tier = policy.get("tier", params.get("model_tier", "balanced"))
    if tier not in _ALLOWED_MODEL_TIERS:
        raise ValidationError({"parameters.model_policy.tier": "Unsupported model tier."})
    allow_fallback = policy.get("allow_fallback", params.get("allow_fallback", True))
    if not isinstance(allow_fallback, bool):
        raise ValidationError({"parameters.model_policy.allow_fallback": "Must be boolean."})
    return {"tier": tier, "allow_fallback": allow_fallback}


def build_idempotency_key(user_id, task_type, project_id, source_id, collection_id, payload, parameters):
    canonical = json.dumps(
        {
            "user_id": user_id,
            "task_type": task_type,
            "project_id": project_id,
            "source_id": source_id,
            "collection_id": collection_id,
            "payload": payload,
            "parameters": parameters,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_job_ownership(user, source=None, collection=None, subject=None, project=None):
    if project and project.owner_id != user.id:
        raise ValidationError({"project": "You do not own this project."})
    if source and source.user_id != user.id:
        raise ValidationError({"source": "You do not own this source."})
    if collection and collection.user_id != user.id:
        raise ValidationError({"collection": "You do not own this collection."})
    if source and collection:
        raise ValidationError("Choose either source or collection, not both.")
    if source and subject and source.subject_id and source.subject_id != subject.id:
        raise ValidationError({"subject": "Subject does not match the selected source."})
    linked_project = getattr(source, "project", None) or getattr(collection, "project", None)
    if linked_project and project and linked_project.id != project.id:
        raise ValidationError({"project": "Project must match the selected source or collection."})


@transaction.atomic
def create_ai_job(*, user, task_type, project=None, source=None, collection=None, subject=None, input_payload=None, parameters=None, force=False):
    parameters = _as_mapping(parameters, "parameters")
    build_model_policy(parameters)
    character = TASK_CHARACTER[task_type]
    project = project or getattr(source, "project", None) or getattr(collection, "project", None)
    validate_job_ownership(user, source, collection, subject, project)
    input_payload = build_task_input(
        user=user,
        task_type=task_type,
        source=source,
        collection=collection,
        subject=subject,
        project=project,
        input_payload=input_payload,
        parameters=parameters,
    )
    key = build_idempotency_key(user.id, task_type, getattr(project, "id", None), getattr(source, "id", None), getattr(collection, "id", None), input_payload, parameters)
    if not force:
        existing = AIJob.objects.filter(user=user, idempotency_key=key).exclude(status__in=[AIJob.Status.FAILED, AIJob.Status.CANCELED]).first()
        if existing:
            return existing, False
    if force:
        key = hashlib.sha256(f"{key}:{timezone.now().isoformat()}".encode()).hexdigest()
    job = AIJob.objects.create(
        user=user,
        project=project,
        character=character,
        task_type=task_type,
        source=source,
        collection=collection,
        subject=subject or getattr(source, "subject", None) or getattr(collection, "subject", None),
        idempotency_key=key,
        input_payload=input_payload,
        parameters=parameters,
        status=AIJob.Status.QUEUED,
    )
    reserve_character_request(user, character, job=job, idempotency_key=key)
    job.credits_reserved = True
    job.save(update_fields=["credits_reserved", "updated_at"])
    StudentSourceInteraction.objects.create(
        user=user,
        source=source,
        collection=collection,
        character=character,
        action={
            AIJob.Character.FAHES: StudentSourceInteraction.Action.CREATE_QUIZ,
            AIJob.Character.KHOTA: StudentSourceInteraction.Action.CREATE_STUDY_PLAN,
            AIJob.Character.RASHEED: StudentSourceInteraction.Action.STUDY_ADVICE,
            AIJob.Character.KHOLASA: StudentSourceInteraction.Action.SUMMARIZE,
            AIJob.Character.SADA: StudentSourceInteraction.Action.VOICE_HELP,
        }[character],
        status=StudentSourceInteraction.Status.CREATED,
        metadata={"ai_job_id": str(job.public_id)},
    ) if source or collection else None
    # The outbox row -- not the Celery message -- is the durable record that
    # this job still needs to be sent. If the broker/worker gap swallows the
    # message below, the reconciliation sweep (tasks.reconcile_stuck_ai_jobs)
    # finds this row and re-dispatches it (spec: "Accepted job loss: 0").
    AIJobDispatchOutbox.objects.create(job=job)
    from .tasks import dispatch_ai_job
    transaction.on_commit(lambda: dispatch_ai_job.delay(job.pk))
    return job, True


def build_service_payload(job):
    return {
        "contract_version": job.contract_version,
        "client_job_id": str(job.public_id),
        "user_id": str(job.user_id),
        "project_id": str(job.project_id) if job.project_id else None,
        "task_type": job.task_type,
        "input": build_task_input(
            user=job.user,
            task_type=job.task_type,
            source=job.source,
            collection=job.collection,
            subject=job.subject,
            project=job.project,
            input_payload=job.input_payload,
            parameters=job.parameters,
        ),
        "model_policy": build_model_policy(job.parameters),
        "trace_context": {"request_id": job.request_id or str(job.public_id)},
    }


def submit_job_to_service(job):
    response = AIServiceClient().create_job(build_service_payload(job))
    data = response.data
    external_job_id = str(data.get("job_id") or data.get("id") or "")
    if not external_job_id:
        raise AIServiceError('AI service did not return a job identifier.', code='invalid_ai_service_response', retryable=False)
    return update_job_progress(
        job,
        AIJob.Status.SUBMITTED,
        external_job_id=external_job_id,
        metadata={"submit_response": data},
    )


@transaction.atomic
def update_job_progress(job, next_status, *, external_job_id=None, metadata=None):
    """Advance a non-terminal job through one allowed server-side transition."""

    job = AIJob.objects.select_for_update().get(pk=job.pk)
    if job.status in TERMINAL_JOB_STATUSES:
        return job
    if next_status in {AIJob.Status.COMPLETED, AIJob.Status.MATERIALIZING, AIJob.Status.OUTPUT_READY}:
        raise ValidationError({"status": "Only complete_job may materialize or complete an AI result."})
    if next_status == job.status:
        return job
    if next_status not in JOB_PROGRESS_TRANSITIONS.get(job.status, set()):
        logger.info("Ignoring out-of-order AI job transition %s -> %s for %s", job.status, next_status, job.public_id)
        return job
    job.status = next_status
    if external_job_id:
        job.external_job_id = external_job_id
    if next_status == AIJob.Status.SUBMITTED:
        job.submitted_at = timezone.now()
    job.last_synced_at = timezone.now()
    if metadata:
        job.service_metadata = {**job.service_metadata, **metadata}
    job.save(update_fields=["external_job_id", "status", "submitted_at", "last_synced_at", "service_metadata", "updated_at"])
    return job


@transaction.atomic
def fail_job(job, error):
    job = AIJob.objects.select_for_update().get(pk=job.pk)
    if job.status in TERMINAL_JOB_STATUSES:
        return job
    job.status = AIJob.Status.FAILED
    job.error_code = getattr(error, "code", ErrorCode.PROVIDER_UNAVAILABLE)
    job.error_message = 'The AI request could not be completed. Please try again.'
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "error_code", "error_message", "completed_at", "updated_at"])
    if job.credits_reserved and not job.credits_committed:
        refund_character_request(job.user, job.character, job=job)
        job.credits_reserved = False
        job.save(update_fields=["credits_reserved", "updated_at"])
    from apps.notifications.models import Notification
    Notification.objects.create(
        user=job.user,
        category=Notification.Category.AI,
        idempotency_key=f"ai-job:{job.public_id}:failed",
        title="تعذر إكمال الطلب الذكي",
        body="لم يكتمل الطلب. لم يتم احتساب الاستخدام المحجوز ويمكنك المحاولة مجددًا.",
        data={"ai_job_id": str(job.public_id), "task_type": job.task_type, "error_code": job.error_code},
        action_url=f"/ai/jobs/{job.public_id}",
    )
    return job


@transaction.atomic
def complete_job(job, result_payload, metadata=None):
    job = AIJob.objects.select_for_update().select_related("user", "project", "source", "collection", "subject").get(pk=job.pk)
    if job.status == AIJob.Status.COMPLETED:
        return job
    if job.status in {AIJob.Status.CANCELED, AIJob.Status.FAILED}:
        logger.warning('Ignoring late AI completion for terminal job %s in status %s.', job.public_id, job.status)
        return job
    job.status = AIJob.Status.MATERIALIZING
    job.save(update_fields=["status", "updated_at"])
    result_type, result_id = materialize_job(job, result_payload)
    if job.credits_reserved and not job.credits_committed:
        commit_character_request(job)
    job.status = AIJob.Status.COMPLETED
    job.result_payload = result_payload
    job.result_type = result_type
    job.result_id = result_id
    job.credits_committed = True
    job.completed_at = timezone.now()
    job.last_synced_at = timezone.now()
    job.service_metadata = {**job.service_metadata, **(metadata or {})}
    job.quality_metrics = dict((metadata or {}).get("quality_metrics") or job.quality_metrics or {})
    job.security_flags = list((metadata or {}).get("security_flags") or job.security_flags or [])
    job.output_schema_version = str(result_payload.get("schema_version") or job.output_schema_version or "")[:20]
    usage = (metadata or {}).get("usage") or {}
    provider = (metadata or {}).get("provider") or {}
    prompt = (metadata or {}).get("prompt") or {}
    job.provider_account = str(provider.get("account") or job.provider_account or "")[:40]
    job.model_name = str(provider.get("model") or job.model_name or "")[:100]
    job.prompt_version = str(prompt.get("version") or job.prompt_version or "")[:40]
    job.input_tokens = int(usage.get("input_tokens") or 0)
    job.output_tokens = int(usage.get("output_tokens") or 0)
    try:
        job.cost_usd = Decimal(str(usage.get("estimated_cost_usd") or 0))
    except InvalidOperation:
        job.cost_usd = Decimal("0")
    job.save()
    StudentSourceInteraction.objects.filter(metadata__ai_job_id=str(job.public_id)).update(
        status=StudentSourceInteraction.Status.COMPLETED,
        result_type=result_type,
        result_id=int(result_id) if str(result_id).isdigit() else None,
    )
    from apps.notifications.models import Notification
    Notification.objects.create(
        user=job.user,
        category=Notification.Category.AI,
        idempotency_key=f"ai-job:{job.public_id}:completed",
        title="اكتملت المعالجة الذكية",
        body="أصبحت نتيجة الطلب جاهزة للمراجعة.",
        data={"ai_job_id": str(job.public_id), "task_type": job.task_type, "result_type": result_type, "result_id": result_id},
        action_url=f"/ai/jobs/{job.public_id}",
    )
    return job


def cancel_job(job):
    external_job_id = ''
    with transaction.atomic():
        job = AIJob.objects.select_for_update().get(pk=job.pk)
        if job.status in TERMINAL_JOB_STATUSES:
            return job
        external_job_id = job.external_job_id
        job.status = AIJob.Status.CANCELED
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at", "updated_at"])
        if job.credits_reserved and not job.credits_committed:
            refund_character_request(job.user, job.character, job=job)
            job.credits_reserved = False
            job.save(update_fields=["credits_reserved", "updated_at"])
    if external_job_id:
        from .tasks import cancel_external_ai_job
        transaction.on_commit(lambda: cancel_external_ai_job.delay(external_job_id))
    return job


def forward_feedback(feedback):
    payload = {
        "client_feedback_id": feedback.id,
        "client_job_id": str(feedback.job.public_id),
        "external_job_id": feedback.job.external_job_id,
        "user_id": feedback.user_id,
        "rating": feedback.rating,
        "is_helpful": feedback.is_helpful,
        "feedback_type": feedback.feedback_type,
        "reason_codes": feedback.reason_codes,
        "comment": feedback.comment,
        "corrected_output": feedback.corrected_output,
        "training_consent": feedback.training_consent,
        "consent_version": feedback.consent_version,
        "provider_snapshot": feedback.provider_snapshot,
        "model_snapshot": feedback.model_snapshot,
        "prompt_version_snapshot": feedback.prompt_version_snapshot,
    }
    AIServiceClient().send_feedback(payload, f"feedback:{feedback.id}")
    feedback.forwarded_to_ai_service = True
    feedback.save(update_fields=["forwarded_to_ai_service", "updated_at"])
