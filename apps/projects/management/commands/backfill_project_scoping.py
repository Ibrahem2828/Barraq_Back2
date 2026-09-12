"""Phased Project-First migration (Baraq_MD_Blueprint/01_BACKEND.md §3.3,
'موصى به'): count existing NULL-project rows, create/reuse one default
Project per affected user, and backfill every child record so no source,
collection, AI job, or generated artifact is left without a project.

This command only backfills existing NULL rows -- it never changes a
project that is already set. It's safe to re-run (idempotent): rows that
already have a project are left untouched, and each user gets at most one
default project (matched by owner + title).

Deliberately stops short of a DB-level NOT NULL migration -- per the
blueprint's own recommended order, that is a separate, later step once a
backfill has actually been run against production data.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.ai_integration.models import AIJob
from apps.analytics.models import StudentRecommendation
from apps.audio.models import Transcription
from apps.projects.models import Project
from apps.quizzes.models import Quiz
from apps.sources.models import StudentSource, StudentSourceCollection
from apps.study_plans.models import StudyPlan
from apps.summaries.models import Summary

DEFAULT_PROJECT_TITLE = "المشروع العام"

# Artifacts inherit their project from their AIJob when possible, else the
# user's default project. (label, model) pairs, applied in this order.
_ARTIFACT_STEPS = (
    ("Quiz", Quiz, "ai_job__project"),
    ("StudyPlan", StudyPlan, "ai_job__project"),
    ("Summary", Summary, "ai_job__project"),
    ("Transcription", Transcription, "ai_job__project"),
    ("StudentRecommendation", StudentRecommendation, "ai_job__project"),
)


class Command(BaseCommand):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._default_project_cache: dict[int, Project] = {}

    help = (
        "Backfill a default Project onto every existing source/collection/AI "
        "job/artifact row that has no project (Project-First migration, "
        "Baraq_MD_Blueprint 01_BACKEND.md §3.3)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report counts of affected rows; write nothing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        User = get_user_model()

        report: dict[str, int] = {}

        collection_qs = StudentSourceCollection.objects.filter(project__isnull=True)
        source_qs = StudentSource.objects.filter(project__isnull=True)
        job_qs = AIJob.objects.filter(project__isnull=True)
        artifact_querysets = {
            label: model.objects.filter(project__isnull=True)
            for label, model, _ in _ARTIFACT_STEPS
        }

        report["StudentSourceCollection"] = collection_qs.count()
        report["StudentSource"] = source_qs.count()
        report["AIJob"] = job_qs.count()
        for label, qs in artifact_querysets.items():
            report[label] = qs.count()

        # .union() rejects an ORDER BY in any of its subqueries -- every
        # model here has a default Meta.ordering, so .order_by() must clear
        # it before .values_list() (Django: "ORDER BY not allowed in
        # subqueries of compound statements").
        affected_user_ids = set(
            collection_qs.order_by().values_list("user_id", flat=True).union(
                source_qs.order_by().values_list("user_id", flat=True),
                job_qs.order_by().values_list("user_id", flat=True),
                *[
                    qs.order_by().values_list("user_id", flat=True)
                    for qs in artifact_querysets.values()
                ],
            )
        )

        self.stdout.write("Project-scoping backfill -- current gaps:")
        for label, count in report.items():
            self.stdout.write(f"  {label}: {count} row(s) without a project")
        self.stdout.write(f"  Affected users: {len(affected_user_ids)}")

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: no changes written."))
            return
        if not affected_user_ids:
            self.stdout.write(self.style.SUCCESS("Nothing to backfill."))
            return

        total_updated = 0
        for user in User.objects.filter(id__in=affected_user_ids).iterator():
            with transaction.atomic():
                total_updated += self._backfill_user(user)
        self.stdout.write(self.style.SUCCESS(f"Backfill complete. {total_updated} row(s) updated."))

    def _backfill_user(self, user) -> int:
        updated = 0

        # 1. Collections: no project to inherit from -- always the default.
        updated += StudentSourceCollection.objects.filter(
            user=user, project__isnull=True
        ).update(project=self._default_project_for(user))

        # 2. Sources: inherit their collection's project when set, else default.
        for source in StudentSource.objects.filter(user=user, project__isnull=True).select_related(
            "collection"
        ):
            source.project = (
                getattr(source.collection, "project", None) or self._default_project_for(user)
            )
            source.save(update_fields=["project", "updated_at"])
            updated += 1

        # 3. AI jobs: inherit from their source or collection, else default.
        for job in AIJob.objects.filter(user=user, project__isnull=True).select_related(
            "source", "collection"
        ):
            job.project = (
                getattr(job.source, "project", None)
                or getattr(job.collection, "project", None)
                or self._default_project_for(user)
            )
            job.save(update_fields=["project", "updated_at"])
            updated += 1

        # 4. Artifacts: inherit from their AIJob, else default.
        for _, model, _ in _ARTIFACT_STEPS:
            for artifact in model.objects.filter(user=user, project__isnull=True).select_related(
                "ai_job"
            ):
                artifact.project = (
                    getattr(artifact.ai_job, "project", None) or self._default_project_for(user)
                )
                artifact.save(update_fields=["project", "updated_at"])
                updated += 1

        return updated

    def _default_project_for(self, user) -> Project:
        cached = self._default_project_cache.get(user.id)
        if cached is not None:
            return cached
        project, _ = Project.objects.get_or_create(
            owner=user,
            title=DEFAULT_PROJECT_TITLE,
            defaults={"status": Project.Status.ACTIVE},
        )
        self._default_project_cache[user.id] = project
        return project
