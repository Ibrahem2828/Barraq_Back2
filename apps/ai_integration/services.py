from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Avg, Count
from django.http import Http404
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
from .error_codes import ErrorCode, is_retryable_error, public_error_message
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


#: The most sources one AI job may operate on. Previously applied as a silent
#: `[:10]` slice during resolution, so selecting eleven sources quietly
#: generated a quiz from ten of them and the learner was never told which.
MAX_SOURCES_PER_JOB = 10


class AIRequestError(ValidationError):
    """A validation error carrying a stable, branchable domain code.

    apps.common.exceptions.domain_error_code promotes `domain_code` to the top
    level of the error envelope, so the client can distinguish "too many
    sources" from any other 400 instead of showing a generic message.
    """

    def __init__(self, detail, *, code):
        self.domain_code = code
        self.code = code
        self.retryable = False
        super().__init__(detail)


def _resolve_sources(*, source=None, collection=None):
    """The exact source set a job operates on, at *creation* time.

    Shared by the per-task input builders so `input.source_ids` records the
    selection. Dispatch does not call this -- it replays the recorded
    selection through resolve_job_sources() instead, so a later library edit
    cannot change what an already-created job meant.
    """
    if source is not None:
        return [source]
    if collection is None:
        return []
    usable = collection.sources.filter(
        status__in=StudentSource.AI_USABLE_STATUSES
    ).order_by("id")
    count = usable.count()
    if not count:
        raise ValidationError({"source": "At least one usable source is required."})
    if count > MAX_SOURCES_PER_JOB:
        # Explicit refusal rather than a silent slice: the learner picked
        # these sources deliberately and must know the request was not honoured.
        raise AIRequestError(
            {
                "source": (
                    f"لا يمكن استخدام أكثر من {MAX_SOURCES_PER_JOB} مصادر في طلب واحد. "
                    f"المجلد يحتوي على {count}."
                ),
                "limit": MAX_SOURCES_PER_JOB,
                "selected": count,
            },
            code="too_many_sources",
        )
    return list(usable)


def _source_ids(*, source=None, collection=None):
    return [str(item.id) for item in _resolve_sources(source=source, collection=collection)]


def resolve_job_sources(job):
    """The sources a job was created against, replayed from its own record.

    `input_payload["source_ids"]` is written once at creation and is the job's
    immutable scope. Re-deriving it from `job.collection` at dispatch -- which
    is what used to happen -- meant moving a source between folders silently
    changed what an accepted job would operate on, and a job could even
    dispatch against material the learner had since removed.

    Scoped to `job.user`, so a recorded id can never widen access beyond the
    owner even if the payload were tampered with.
    """
    recorded = (job.input_payload or {}).get("source_ids") or []
    if not recorded:
        # Two legitimate cases, neither of which records a list:
        #  - Sada, whose input carries a singular `source_id`;
        #  - jobs created before this scope record existed.
        # A single-source job's scope *is* its FK, and that FK is already
        # immutable, so falling back to it is both correct and keeps in-flight
        # jobs dispatchable. Rasheed has neither and legitimately yields [].
        return [job.source] if job.source_id else []
    try:
        wanted = [int(value) for value in recorded]
    except (TypeError, ValueError) as exc:
        raise AIRequestError(
            {"source": "The job's recorded source scope is unreadable."},
            code="invalid_source_scope",
        ) from exc

    by_id = {
        item.id: item
        for item in StudentSource.objects.filter(id__in=wanted, user_id=job.user_id)
    }
    missing = [value for value in wanted if value not in by_id]
    if missing:
        # Deleted between acceptance and dispatch. Failing loudly beats
        # silently generating from whatever survived.
        raise AIRequestError(
            {"source": "A source this request was created against no longer exists."},
            code="source_no_longer_available",
        )
    return [by_id[value] for value in wanted]


def _input_source_ids(payload, *, source=None, collection=None):
    """The `input.source_ids` for a task input.

    On a replay -- build_service_payload rebuilding the input at dispatch from
    the stored payload -- the recorded ids are authoritative and are returned
    as-is. Re-deriving them there would let a folder edit change an accepted
    job's scope, and would also raise `too_many_sources` at dispatch for a
    folder that merely grew after the job was accepted.
    """
    recorded = payload.get("source_ids")
    if isinstance(recorded, list) and recorded:
        return [str(value) for value in recorded]
    return _source_ids(source=source, collection=collection)


def content_sha256(source):
    """Moved from views.py so build_service_payload() (below) can reuse it
    without a services -> views layering inversion. Caches the digest on
    source.metadata so it is computed from the file at most once."""
    checksum = str((source.metadata or {}).get("sha256") or "").lower()
    if len(checksum) == 64 and all(character in "0123456789abcdef" for character in checksum):
        return checksum
    if not source.file:
        raise Http404
    digest = hashlib.sha256()
    with source.file.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = digest.hexdigest()
    source.metadata = {**(source.metadata or {}), "sha256": checksum}
    source.save(update_fields=["metadata", "updated_at"])
    return checksum


def _pinned_source_versions(job, sources):
    """Return the immutable source versions captured when the job was accepted.

    Existing pre-upgrade queued jobs have no snapshot and retain the previous
    dispatch-time behavior. New jobs always carry a snapshot, and dispatch
    refuses to substitute newer bytes under the same source id.
    """
    recorded = (job.input_payload or {}).get("source_versions")
    if not isinstance(recorded, dict):
        return {str(item.id): content_sha256(item) for item in sources}

    expected_ids = {str(item.id) for item in sources}
    if set(recorded) != expected_ids:
        raise AIRequestError(
            {"source": "The job's recorded source versions do not match its source scope."},
            code="invalid_source_scope",
        )
    normalized = {}
    for item in sources:
        source_id = str(item.id)
        expected = str(recorded.get(source_id) or "").lower()
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
            raise AIRequestError(
                {"source": "The job's recorded source version is invalid."},
                code="invalid_source_scope",
            )
        if content_sha256(item) != expected:
            raise AIRequestError(
                {"source": "A selected source changed after this job was created."},
                code=ErrorCode.SOURCE_VERSION_CHANGED,
            )
        normalized[source_id] = expected
    return normalized


def build_fahes_job_input(*, source=None, collection=None, subject=None, input_payload=None, parameters=None):
    payload = _as_mapping(input_payload, "input")
    params = _as_mapping(parameters, "parameters")
    input_data = {
        "source_ids": _input_source_ids(payload, source=source, collection=collection),
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
        "source_ids": _input_source_ids(payload, source=source, collection=collection),
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
        "source_ids": _input_source_ids(payload, source=source, collection=collection),
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


def validate_selected_sources(user, sources, *, project=None):
    """Authorize an explicit multi-source selection.

    Every source is checked -- ownership, project membership and AI
    eligibility -- not just the first. Browser-supplied ids are never trusted:
    a source the learner does not own, or one from another project, is
    refused rather than filtered out silently, so a selection that was not
    honoured can never look like one that was.
    """
    if not sources:
        raise ValidationError({"source_ids": "At least one source is required."})
    if len(sources) > MAX_SOURCES_PER_JOB:
        raise AIRequestError(
            {
                "source_ids": (
                    f"لا يمكن استخدام أكثر من {MAX_SOURCES_PER_JOB} مصادر في طلب واحد."
                ),
                "limit": MAX_SOURCES_PER_JOB,
                "selected": len(sources),
            },
            code="too_many_sources",
        )
    if len({item.id for item in sources}) != len(sources):
        raise ValidationError({"source_ids": "A source may only be selected once."})
    for item in sources:
        if item.user_id != user.id:
            raise ValidationError({"source_ids": "You do not own one of the selected sources."})
        if item.status not in StudentSource.AI_USABLE_STATUSES:
            raise AIRequestError(
                {"source_ids": f'"{item.title}" غير جاهز للاستخدام مع الذكاء الاصطناعي.'},
                code="source_not_ready",
            )
    projects = {item.project_id for item in sources}
    if len(projects) > 1:
        raise ValidationError(
            {"source_ids": "All selected sources must belong to the same project."}
        )
    selected_project_id = next(iter(projects))
    if project is not None and selected_project_id and selected_project_id != project.id:
        raise ValidationError(
            {"source_ids": "Selected sources must belong to the requested project."}
        )
    return sources


def validate_job_ownership(user, source=None, collection=None, subject=None, project=None):
    if project is None:
        # Blueprint 01_BACKEND.md §3.1/§3.3: no AI job may be created without
        # a project. This is server-side defense-in-depth -- create_ai_job()
        # already inherits `project` from source/collection before calling
        # this, but any other caller must not be able to skip it.
        raise ValidationError({"project": "A project is required for this request."})
    if project.owner_id != user.id:
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
def create_ai_job(*, user, task_type, project=None, source=None, collection=None, sources=None, subject=None, input_payload=None, parameters=None, force=False):
    parameters = _as_mapping(parameters, "parameters")
    build_model_policy(parameters)
    character = TASK_CHARACTER[task_type]
    if sources:
        # An explicit multi-source selection: an ephemeral scope that exists
        # only on this job. The web client used to express "these three
        # sources" by bulk-reassigning them into a collection, permanently
        # reorganising the learner's library to describe one request.
        if source is not None or collection is not None:
            raise ValidationError(
                {"source_ids": "Choose either explicit sources, one source, or one collection."}
            )
        sources = validate_selected_sources(user, list(sources), project=project)
        project = project or sources[0].project
        subject = subject or sources[0].subject
        # Seeding the record here is what makes it authoritative: the task
        # builders return it untouched (see _input_source_ids) and dispatch
        # replays it (see resolve_job_sources), so no FK is needed to carry a
        # scope the job already describes.
        input_payload = {
            **_as_mapping(input_payload, "input"),
            "source_ids": [str(item.id) for item in sources],
        }
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
    scope_sources = list(sources) if sources else _resolve_sources(
        source=source, collection=collection
    )
    input_payload = {
        **input_payload,
        "source_versions": {
            str(item.id): content_sha256(item) for item in scope_sources
        },
    }
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
    # Blueprint 01_BACKEND.md §5.2: the internal contract requires source_ids
    # and source_versions (a source_id -> content sha256 map) alongside the
    # rest of the payload, so the AI service pins exactly which source
    # content it is operating on at dispatch time -- not a separate,
    # later, out-of-band pull that could race a source being changed.
    #
    # Replayed from the job's own recorded scope rather than re-derived from
    # job.collection. Re-deriving meant a folder edit between acceptance and
    # dispatch silently changed which material the job ran on.
    sources = resolve_job_sources(job)
    source_ids = [str(item.id) for item in sources]
    source_versions = _pinned_source_versions(job, sources)
    return {
        "contract_version": job.contract_version,
        "client_job_id": str(job.public_id),
        "user_id": str(job.user_id),
        "project_id": str(job.project_id) if job.project_id else None,
        "task_type": job.task_type,
        "source_ids": source_ids,
        "source_versions": source_versions,
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


#: The public progress vocabulary clients render.
#:
#: Deliberately its own vocabulary rather than the AI service's internal
#: JobStatus: that enum is private, changes with pipeline internals, and
#: distinguishes stages (planning vs generating, repairing vs validating) that
#: mean nothing to a learner. This is the mapping layer between them.
class ProgressStage:
    QUEUED = "queued"
    PREPARING = "preparing"
    RETRIEVING = "retrieving"
    GENERATING = "generating"
    VALIDATING = "validating"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


#: AI-internal stage -> (public stage, the Django status it implies).
#:
#: The AI service reports eleven states; Django's own status vocabulary is
#: coarser and its transition table only accepts a few of them. Before this
#: table existed, `refresh` matched on Django's names -- of which the AI
#: emits exactly one ("validating") -- so a job sat at `submitted` for its
#: whole life and the web app filled the silence with a hardcoded percentage.
AI_STAGE_MAP = {
    "queued": (ProgressStage.QUEUED, AIJob.Status.SUBMITTED),
    "preparing": (ProgressStage.PREPARING, AIJob.Status.PROCESSING),
    "retrieving": (ProgressStage.RETRIEVING, AIJob.Status.PROCESSING),
    "planning": (ProgressStage.GENERATING, AIJob.Status.PROCESSING),
    "generating": (ProgressStage.GENERATING, AIJob.Status.PROCESSING),
    "repairing": (ProgressStage.VALIDATING, AIJob.Status.VALIDATING),
    "validating": (ProgressStage.VALIDATING, AIJob.Status.VALIDATING),
    "materializing": (ProgressStage.FINALIZING, AIJob.Status.VALIDATING),
    # Django's own status names, which the webhook accepted before this map
    # existed. Kept so an older AI build -- or any caller still sending the
    # previous vocabulary -- keeps reporting progress instead of being
    # rejected as unrecognized.
    "submitted": (ProgressStage.QUEUED, AIJob.Status.SUBMITTED),
    "processing": (ProgressStage.GENERATING, AIJob.Status.PROCESSING),
}

#: Django status -> public stage, for a job the AI has not reported on yet.
DJANGO_STATUS_STAGE = {
    AIJob.Status.CREATED: ProgressStage.QUEUED,
    AIJob.Status.QUEUED: ProgressStage.QUEUED,
    AIJob.Status.SUBMITTED: ProgressStage.QUEUED,
    AIJob.Status.PROCESSING: ProgressStage.GENERATING,
    AIJob.Status.VALIDATING: ProgressStage.VALIDATING,
    AIJob.Status.OUTPUT_READY: ProgressStage.FINALIZING,
    AIJob.Status.MATERIALIZING: ProgressStage.FINALIZING,
    AIJob.Status.COMPLETED: ProgressStage.COMPLETED,
    AIJob.Status.FAILED: ProgressStage.FAILED,
    AIJob.Status.CANCELED: ProgressStage.CANCELED,
}

#: Ordering used to reject a stage that would move backwards. A slow or
#: duplicated poll must never walk a learner's progress back to "retrieving"
#: after it reached "generating".
_STAGE_ORDER = [
    ProgressStage.QUEUED,
    ProgressStage.PREPARING,
    ProgressStage.RETRIEVING,
    ProgressStage.GENERATING,
    ProgressStage.VALIDATING,
    ProgressStage.FINALIZING,
]


def public_progress_stage(job):
    """The stage a client should render for this job.

    Terminal Django statuses always win: a stale poll arriving after
    completion cannot resurrect an in-progress stage.
    """
    if job.status in TERMINAL_JOB_STATUSES:
        return DJANGO_STATUS_STAGE[job.status]
    recorded = str((job.service_metadata or {}).get("progress_stage") or "")
    if recorded in _STAGE_ORDER:
        return recorded
    return DJANGO_STATUS_STAGE.get(job.status, ProgressStage.QUEUED)


def record_ai_stage(job, remote_stage):
    """Advance a job's public stage from an AI-reported internal stage.

    Returns the job. Unknown stages and backwards moves are ignored rather
    than applied, so an added AI state or an out-of-order poll degrades to
    "no change" instead of corrupting what the learner sees.
    """
    mapped = AI_STAGE_MAP.get(str(remote_stage or "").lower())
    if mapped is None:
        return job
    stage, django_status = mapped
    if job.status in TERMINAL_JOB_STATUSES:
        # Completed, failed and canceled are final. A slow poll arriving
        # afterwards must not reopen the job or rewind what the learner sees.
        return job
    current = public_progress_stage(job)
    if current in _STAGE_ORDER and _STAGE_ORDER.index(stage) < _STAGE_ORDER.index(current):
        return job

    job = update_job_progress(job, django_status, metadata={"progress_stage": stage})
    # update_job_progress returns early when the Django status is unchanged,
    # and several stages share one: preparing, retrieving and generating are
    # all PROCESSING. Without this the learner would sit on "preparing" for
    # the whole run while the pipeline moved on beneath them.
    if job.status not in TERMINAL_JOB_STATUSES and (
        (job.service_metadata or {}).get("progress_stage") != stage
    ):
        job.service_metadata = {**(job.service_metadata or {}), "progress_stage": stage}
        job.last_synced_at = timezone.now()
        job.save(update_fields=["service_metadata", "last_synced_at", "updated_at"])
    return job


@transaction.atomic
def fail_job(job, error):
    job = AIJob.objects.select_for_update().get(pk=job.pk)
    if job.status in TERMINAL_JOB_STATUSES:
        return job
    job.status = AIJob.Status.FAILED
    candidate_code = str(getattr(error, "code", ""))
    job.error_code = (
        candidate_code if candidate_code in ErrorCode.values_set() else ErrorCode.PROVIDER_UNAVAILABLE
    )
    job.error_message = public_error_message(job.error_code)
    job.service_metadata = {
        **(job.service_metadata or {}),
        "failure": {
            "code": job.error_code,
            "retryable": is_retryable_error(job.error_code),
        },
    }
    job.completed_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "error_code",
            "error_message",
            "service_metadata",
            "completed_at",
            "updated_at",
        ]
    )
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
    # select_for_update() cannot be combined with select_related() on a
    # nullable relation -- Postgres rejects it outright ("FOR UPDATE cannot
    # be applied to the nullable side of an outer join"), because it can't
    # lock a row that might not exist on the NULL side of the join. Only
    # `user` is a non-nullable FK here; project/source/collection/subject
    # are all null=True, so they stay off the locked queryset and are
    # fetched normally (lazily, on first access) instead.
    job = AIJob.objects.select_for_update().select_related("user").get(pk=job.pk)
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
        transaction.on_commit(
            lambda: cancel_external_ai_job.delay(external_job_id, str(job.user_id))
        )
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
