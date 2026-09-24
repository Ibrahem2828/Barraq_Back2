from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.analytics.models import StudentRecommendation
from apps.audio.models import Transcription
from apps.quizzes.models import (
    Choice,
    DifficultyLevelChoices,
    GenerationTypeChoices,
    Question,
    QuestionBankItem,
    QuestionTypeChoices,
    Quiz,
    QuizStatusChoices,
    QuizTypeChoices,
)
from apps.sources.models import StudentSource
from apps.study_plans.models import StudyPlan, StudyPlanProgressLog, StudyTask
from apps.summaries.models import Summary


def _subject(job):
    return (
        job.subject
        or getattr(job.source, "subject", None)
        or getattr(job.collection, "subject", None)
        or getattr(job.project, "subject", None)
    )


def _validate_question(item, index):
    if not isinstance(item, dict):
        raise ValidationError(f"Question {index} must be an object.")
    question_text = str(item.get("question") or "").strip()
    if len(question_text) < 3 or len(question_text) > 4000:
        raise ValidationError(f"Question {index} has invalid text length.")
    question_type = item.get("question_type", QuestionTypeChoices.MCQ)
    if question_type not in {QuestionTypeChoices.MCQ, QuestionTypeChoices.TRUE_FALSE}:
        raise ValidationError(f"Question {index} uses an unsupported question type.")
    difficulty = item.get("difficulty") or DifficultyLevelChoices.MEDIUM
    if difficulty not in dict(DifficultyLevelChoices.choices):
        raise ValidationError(f"Question {index} has an invalid difficulty.")
    choices = item.get("choices") or []
    if len(choices) < 2 or len(choices) > 8:
        raise ValidationError(f"Question {index} requires between two and eight choices.")
    correct = item.get("correct_answer_index")
    if isinstance(correct, bool) or not isinstance(correct, int) or correct < 0 or correct >= len(choices):
        raise ValidationError(f"Question {index} has an invalid correct answer index.")
    normalized = [str(value).strip().casefold() for value in choices]
    if any(not value for value in normalized):
        raise ValidationError(f"Question {index} contains an empty choice.")
    if len(normalized) != len(set(normalized)):
        raise ValidationError(f"Question {index} contains duplicate choices.")


@transaction.atomic
def materialize_fahes(job, data):
    subject = _subject(job)
    if subject is None:
        raise ValidationError("A subject is required to materialize a quiz.")
    questions = data.get("questions") or []
    if not questions:
        raise ValidationError("AI output did not contain questions.")
    if len(questions) > 100:
        raise ValidationError("AI output contains too many questions.")
    for index, item in enumerate(questions, start=1):
        _validate_question(item, index)
    difficulty = str(job.parameters.get("difficulty") or DifficultyLevelChoices.MEDIUM)
    if difficulty not in dict(DifficultyLevelChoices.choices):
        difficulty = DifficultyLevelChoices.MEDIUM
    quiz_type = job.parameters.get("quiz_type") or QuizTypeChoices.PRACTICE
    if quiz_type not in dict(QuizTypeChoices.choices):
        quiz_type = QuizTypeChoices.PRACTICE
    time_limit = job.parameters.get("time_limit_minutes")
    if time_limit is not None:
        time_limit = max(1, min(int(time_limit), 600))
    quiz = Quiz.objects.create(
        user=job.user,
        project=job.project,
        subject=subject,
        title=str(data.get("title") or data.get("quiz_title") or f"اختبار {subject.name}")[:255],
        description=str(data.get("description") or "تم إنشاء الاختبار بواسطة فاحص."),
        topic=str(job.input_payload.get("topic") or job.parameters.get("topic") or subject.name),
        difficulty_level=difficulty,
        quiz_type=quiz_type,
        generation_type=GenerationTypeChoices.AI,
        status=QuizStatusChoices.PUBLISHED,
        questions_count=len(questions),
        time_limit_minutes=time_limit,
        ai_request_id=str(job.public_id),
        # Database-enforced duplicate-materialization guard, same as
        # StudentRecommendation/Summary/Transcription below.
        ai_job=job,
    )
    for order, item in enumerate(questions, start=1):
        question = Question.objects.create(
            quiz=quiz,
            text=str(item["question"]),
            question_type=item.get("question_type") or "mcq",
            difficulty_level=item.get("difficulty") or difficulty,
            explanation=str(item.get("explanation") or ""),
            order=order,
            points=max(int(item.get("points") or 1), 1),
        )
        created_choices = []
        correct_index = item.get("correct_answer_index")
        for choice_index, choice_text in enumerate(item.get("choices") or []):
            created_choices.append(Choice.objects.create(
                question=question,
                text=str(choice_text),
                is_correct=choice_index == correct_index,
                order=choice_index + 1,
            ))
        QuestionBankItem.objects.create(
            subject=subject,
            created_by=job.user,
            text=question.text,
            question_type=question.question_type,
            difficulty_level=question.difficulty_level,
            explanation=question.explanation,
            metadata={
                "quiz_id": quiz.id,
                "ai_job_id": str(job.public_id),
                "topic": item.get("topic"),
                "source_reference": item.get("source_reference"),
                "choices": [{"text": c.text, "order": c.order, "is_correct": c.is_correct} for c in created_choices],
            },
            is_public=False,
            is_active=True,
        )
    return "quiz", str(quiz.id)


@transaction.atomic
def materialize_khota(job, data):
    subject = _subject(job)
    if subject is None:
        raise ValidationError("A subject is required to materialize a study plan.")
    days = data.get("plan_days") or []
    if not days:
        raise ValidationError("AI output did not contain plan days.")
    dates = [date.fromisoformat(str(item["date"])) for item in days]
    requested_start = job.input_payload.get("start_date") or job.parameters.get("start_date")
    requested_end = job.input_payload.get("end_date") or job.parameters.get("end_date")
    start = date.fromisoformat(str(requested_start)) if requested_start else min(dates)
    end = date.fromisoformat(str(requested_end)) if requested_end else max(dates)
    requested_daily_minutes = (
        job.input_payload.get("daily_available_minutes")
        or job.parameters.get("daily_minutes")
        or 60
    )
    daily_limit = max(15, min(int(requested_daily_minutes), 720))
    plan = StudyPlan.objects.create(
        user=job.user,
        project=job.project,
        subject=subject,
        title=str(data.get("title") or data.get("plan_title") or f"خطة {subject.name}")[:255],
        description=str(data.get("strategy_summary") or data.get("summary") or ""),
        start_date=start,
        end_date=end,
        daily_study_minutes=daily_limit,
        goal=str(job.input_payload.get("goal") or ""),
        difficulty_level=job.parameters.get("difficulty") or "medium",
        status=StudyPlan.Status.ACTIVE,
        generation_type=StudyPlan.GenerationType.AI,
        ai_request_id=str(job.public_id),
        ai_job=job,
    )
    per_day = defaultdict(int)
    created_tasks = 0
    for day in days:
        task_date = date.fromisoformat(str(day["date"]))
        if not (start <= task_date <= end):
            raise ValidationError("AI output contains a task date outside the requested plan.")
        used = 0
        for item in day.get("tasks") or []:
            minutes = int(item.get("estimated_minutes") or 0)
            if minutes <= 0:
                raise ValidationError("AI output contains a task with an invalid duration.")
            if used + minutes > daily_limit:
                raise ValidationError("AI output exceeds the requested daily study limit.")
            used += minutes
            per_day[task_date] += 1
            priority = item.get("priority") or StudyTask.Priority.MEDIUM
            if priority not in dict(StudyTask.Priority.choices):
                priority = StudyTask.Priority.MEDIUM
            StudyTask.objects.create(
                plan=plan,
                title=str(item.get("topic") or item.get("subject_name") or subject.name)[:255],
                description=str(item.get("reason") or ""),
                task_date=task_date,
                estimated_minutes=minutes,
                priority=priority,
                status=StudyTask.Status.PENDING,
                order=per_day[task_date],
            )
            created_tasks += 1
            if created_tasks > 500:
                raise ValidationError("AI output contains too many study tasks.")
    if created_tasks == 0:
        raise ValidationError("AI output did not contain any valid study tasks.")
    StudyPlanProgressLog.objects.create(
        user=job.user,
        plan=plan,
        action=StudyPlanProgressLog.Action.PLAN_CREATED,
        metadata={"generation_type": "ai", "ai_job_id": str(job.public_id)},
    )
    return "study_plan", str(plan.id)


@transaction.atomic
def materialize_rasheed(job, data):
    topic_performance = job.input_payload.get("topic_performance") or []
    scores = [
        float(item["score"])
        for item in topic_performance
        if isinstance(item, dict) and item.get("score") is not None
    ]
    if scores:
        overall_score = sum(scores) / len(scores)
    else:
        overall_score = next(
            (
                float(item["value"])
                for item in (job.input_payload.get("metrics") or [])
                if isinstance(item, dict)
                and item.get("name") == "average_quiz_percentage"
                and item.get("value") is not None
            ),
            None,
        )
    next_best_action = data.get("next_best_action") or ""
    if isinstance(next_best_action, str):
        next_best_action = {"label": next_best_action} if next_best_action.strip() else {}
    recommendation = StudentRecommendation.objects.create(
        user=job.user,
        project=job.project,
        subject=_subject(job),
        ai_job=job,
        title=str(data.get("title") or "توصيات رشيد")[:255],
        summary=str(data.get("performance_summary") or data.get("summary") or ""),
        overall_score=Decimal(str(overall_score)) if overall_score is not None else None,
        strengths=data.get("strengths") or [],
        weaknesses=data.get("weaknesses") or [],
        recommendations=data.get("recommendations") or [],
        next_best_action=next_best_action,
        source_metrics=data.get("source_metrics") or {
            "metrics": job.input_payload.get("metrics") or [],
            "topic_performance": topic_performance,
        },
    )
    return "recommendation", str(recommendation.id)


@transaction.atomic
def materialize_kholasa(job, data):
    summary = Summary.objects.create(
        user=job.user,
        project=job.project,
        source=job.source,
        collection=job.collection,
        ai_job=job,
        title=str(data.get("title") or "خلاصة المحتوى")[:255],
        short_summary=str(data.get("executive_summary") or data.get("short_summary") or ""),
        detailed_summary=str(data.get("detailed_summary") or ""),
        key_points=data.get("key_points") or [],
        important_terms=data.get("important_terms") or [],
        covered_topics=data.get("covered_topics") or [],
        review_questions=data.get("review_questions") or [],
        source_references=data.get("citations") or data.get("source_references") or [],
        quality_score=data.get("quality_score"),
    )
    return "summary", str(summary.id)


@transaction.atomic
def materialize_sada(job, data):
    if job.source is None:
        raise ValidationError("An audio source is required to materialize a transcription.")
    transcript = str(data.get("full_transcript") or "").strip()
    if not transcript:
        raise ValidationError("AI output did not contain a transcript.")
    cleaned_transcript = str(data.get("cleaned_transcript") or "")
    transcription = Transcription.objects.create(
        user=job.user,
        project=job.project,
        source=job.source,
        ai_job=job,
        title=str(data.get("title") or (job.source.title if job.source else "تفريغ صوتي"))[:255],
        language=str(data.get("language") or "ar"),
        full_transcript=transcript,
        cleaned_transcript=cleaned_transcript,
        segments=data.get("segments") or [],
        detected_topics=data.get("detected_topics") or [],
        duration_seconds=max(int(data.get("duration_seconds") or 0), 0),
        confidence_score=data.get("confidence_score"),
    )
    derived_source_id = _create_derived_text_source(job, transcription, cleaned_transcript or transcript)
    if derived_source_id:
        # Mutates the same dict complete_job() later saves as
        # job.result_payload, so the mobile client immediately sees where
        # the derived source landed without a second round trip.
        data["derived_source_id"] = derived_source_id
    return "transcription", str(transcription.id)


def _create_derived_text_source(job, transcription, cleaned_text):
    """Sada's whole point in the Learning Loop is that its output becomes a
    normal text Source Kholasa/Fahes can select (spec sections 06/19/42).

    The derived row must be a *complete* source, not a UI-visible record. It
    previously carried ``extracted_text`` with no ``file``, so the first
    downstream job died in ``content_sha256`` -- which raises Http404 on a
    source without a file -- before it ever reached the AI service. The outbox
    then retried the dispatch to exhaustion and the learner was finally told
    "provider unavailable", which was not remotely what happened. Every other
    source in the system is bytes plus a content hash, and this one is now the
    same: one source contract, no special read path.

    Reuses the generic ``metadata`` JSONField already on StudentSource for
    traceability instead of adding a new column.
    """

    text = cleaned_text.strip()
    if not text:
        return None

    # Idempotency. complete_job() already returns early for a job that is
    # COMPLETED, and the webhook layer dedupes on event_id, but a derived
    # source is user-visible library content: a second one would be a
    # duplicate the learner has to clean up. Keyed on the job so a replay
    # through any path resolves to the same row.
    existing = StudentSource.objects.filter(
        user=job.user, metadata__ai_job_id=str(job.public_id), metadata__derived_from="sada"
    ).first()
    if existing is not None:
        return str(existing.id)

    title = f"{transcription.title} (تفريغ منظف)"[:255]
    # Deterministic bytes: the same transcript always produces the same file
    # and therefore the same content hash, so the AI service's ingestion cache
    # keys on it exactly as it would for an uploaded file.
    payload = text.encode("utf-8")
    filename = f"sada-{job.public_id}.txt"
    derived = StudentSource(
        user=job.user,
        project=job.project,
        subject=job.subject or getattr(job.source, "subject", None),
        # Inherit the audio source's folder so the transcript lands beside the
        # recording it came from rather than loose in the library root.
        collection=getattr(job.source, "collection", None),
        title=title,
        description="مصدر نصي مشتق تلقائيًا من تفريغ صدى.",
        source_type=StudentSource.SourceType.TEXT,
        original_filename=filename,
        file_size=len(payload),
        mime_type="text/plain",
        extension="txt",
        status=StudentSource.Status.READY,
        extracted_text=text,
        metadata={
            "derived_from": "sada",
            "ai_job_id": str(job.public_id),
            "transcription_id": transcription.id,
            # Storage accounting still counts this row, same as before: the
            # aggregate in subscriptions.services sums file_size across every
            # StudentSource. Whether a system-generated artifact should be
            # billed alongside the audio it came from is a product decision,
            # not a bug, so behaviour is unchanged -- this flag is what a
            # later policy would filter on.
            "generated_artifact": True,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    derived.file.save(filename, ContentFile(payload), save=True)
    return str(derived.id)


def materialize_job(job, data):
    mapping = {
        job.TaskType.FAHES_GENERATE_QUIZ: materialize_fahes,
        job.TaskType.KHOTA_GENERATE_PLAN: materialize_khota,
        job.TaskType.RASHEED_RECOMMENDATIONS: materialize_rasheed,
        job.TaskType.KHOLASA_GENERATE_SUMMARY: materialize_kholasa,
        job.TaskType.SADA_TRANSCRIBE_AUDIO: materialize_sada,
    }
    return mapping[job.task_type](job, data)
