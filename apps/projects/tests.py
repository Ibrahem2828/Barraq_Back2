from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.ai_integration.models import AIJob
from apps.analytics.models import StudentRecommendation
from apps.audio.models import Transcription
from apps.sources.models import StudentSource, StudentSourceCollection
from apps.summaries.models import Summary

from .management.commands.backfill_project_scoping import DEFAULT_PROJECT_TITLE
from .models import Project, ProjectActivity


class ProjectApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="owner@example.com", full_name="Owner User", password="A-safe-password-123"
        )
        self.other = get_user_model().objects.create_user(
            email="other@example.com", full_name="Other User", password="A-safe-password-123"
        )
        self.client.force_authenticate(self.user)

    def test_owner_can_create_archive_and_soft_delete_project(self):
        response = self.client.post("/api/v1/projects/", {"title": "Biology"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        project = Project.objects.get(public_id=response.data["public_id"])
        self.assertEqual(project.owner_id, self.user.id)
        self.assertTrue(ProjectActivity.objects.filter(project=project, event_type="project.created").exists())

        response = self.client.post(f"/api/v1/projects/{project.public_id}/archive/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.ARCHIVED)

        response = self.client.delete(f"/api/v1/projects/{project.public_id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        project.refresh_from_db()
        self.assertTrue(project.is_deleted)

    def test_project_is_invisible_to_other_user(self):
        project = Project.objects.create(owner=self.user, title="Private workspace")
        self.client.force_authenticate(self.other)
        response = self.client.get(f"/api/v1/projects/{project.public_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class BackfillProjectScopingCommandTests(TestCase):
    """apps/projects/management/commands/backfill_project_scoping.py --
    the phased Project-First migration recommended by
    Baraq_MD_Blueprint/01_BACKEND.md §3.3."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email='backfill-owner@example.com', full_name='Backfill Owner', password='A-safe-password-123'
        )

    def _make_orphan_chain(self):
        """One of each model, all with project=None, wired together the way
        a real request would (collection -> source -> AI job -> artifacts),
        so the command's inheritance logic has something real to follow."""
        collection = StudentSourceCollection.objects.create(user=self.user, name='Orphan Folder')
        source = StudentSource.objects.create(
            user=self.user,
            collection=collection,
            title='Orphan source',
            source_type=StudentSource.SourceType.TEXT,
            file='student_sources/orphan.txt',
            original_filename='orphan.txt',
            file_size=10,
            mime_type='text/plain',
            extension='txt',
            status=StudentSource.Status.READY,
        )
        job = AIJob.objects.create(
            user=self.user,
            source=source,
            character=AIJob.Character.KHOLASA,
            task_type=AIJob.TaskType.KHOLASA_GENERATE_SUMMARY,
            status=AIJob.Status.PROCESSING,
            idempotency_key='backfill-chain-job',
        )
        summary = Summary.objects.create(user=self.user, source=source, ai_job=job, title='Orphan summary')
        return collection, source, job, summary

    def test_dry_run_reports_counts_without_writing(self):
        self._make_orphan_chain()
        call_command('backfill_project_scoping', '--dry-run')
        self.assertEqual(StudentSourceCollection.objects.filter(project__isnull=True).count(), 1)
        self.assertEqual(Project.objects.count(), 0)

    def test_backfill_wires_the_whole_inheritance_chain_to_one_default_project(self):
        collection, source, job, summary = self._make_orphan_chain()
        call_command('backfill_project_scoping')

        collection.refresh_from_db()
        source.refresh_from_db()
        job.refresh_from_db()
        summary.refresh_from_db()

        default_project = Project.objects.get(owner=self.user, title=DEFAULT_PROJECT_TITLE)
        # The collection has nothing to inherit from -- it gets the default.
        self.assertEqual(collection.project_id, default_project.id)
        # The source inherits its collection's (now-backfilled) project,
        # rather than getting a second, different default assignment.
        self.assertEqual(source.project_id, default_project.id)
        # The job inherits from its source; the summary inherits from its job.
        self.assertEqual(job.project_id, default_project.id)
        self.assertEqual(summary.project_id, default_project.id)
        self.assertEqual(Project.objects.filter(owner=self.user).count(), 1)

    def test_backfill_does_not_touch_rows_that_already_have_a_project(self):
        existing_project = Project.objects.create(owner=self.user, title='Existing Biology Project')
        source = StudentSource.objects.create(
            user=self.user,
            project=existing_project,
            title='Already scoped source',
            source_type=StudentSource.SourceType.TEXT,
            file='student_sources/scoped.txt',
            original_filename='scoped.txt',
            file_size=5,
            mime_type='text/plain',
            extension='txt',
            status=StudentSource.Status.READY,
        )
        call_command('backfill_project_scoping')
        source.refresh_from_db()
        self.assertEqual(source.project_id, existing_project.id)
        # No default project was ever needed for this user.
        self.assertFalse(Project.objects.filter(owner=self.user, title=DEFAULT_PROJECT_TITLE).exists())

    def test_backfill_is_idempotent(self):
        self._make_orphan_chain()
        call_command('backfill_project_scoping')
        call_command('backfill_project_scoping')
        self.assertEqual(Project.objects.filter(owner=self.user, title=DEFAULT_PROJECT_TITLE).count(), 1)

    def test_orphan_recommendation_and_transcription_are_backfilled_too(self):
        # Analytics/audio artifacts have no source/collection concept of
        # their own -- only their AIJob link -- covering the two models the
        # blueprint audit found missing a project column entirely.
        job = AIJob.objects.create(
            user=self.user,
            character=AIJob.Character.RASHEED,
            task_type=AIJob.TaskType.RASHEED_RECOMMENDATIONS,
            status=AIJob.Status.PROCESSING,
            idempotency_key='backfill-rasheed-job',
        )
        recommendation = StudentRecommendation.objects.create(user=self.user, ai_job=job, title='Orphan recommendation')
        transcription = Transcription.objects.create(
            user=self.user, ai_job=job, title='Orphan transcript', full_transcript='...'
        )
        call_command('backfill_project_scoping')
        recommendation.refresh_from_db()
        transcription.refresh_from_db()
        job.refresh_from_db()
        self.assertIsNotNone(job.project_id)
        self.assertEqual(recommendation.project_id, job.project_id)
        self.assertEqual(transcription.project_id, job.project_id)


class StageProjectsTests(APITestCase):
    """Choosing a stage (e.g. بكالوريا) gives one ready project per subject."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from apps.subjects.models import EducationStage, Subject

        self.User = get_user_model()
        self.stage = EducationStage.objects.get(name="بكالوريا")  # seeded by subjects.0003
        self.Subject = Subject
        self.learner = self.User.objects.create_user(
            email="bac@example.com", password="StrongPass123!", full_name="طالب بكالوريا"
        )

    def _setup_profile(self, stage):
        self.client.force_authenticate(self.learner)
        return self.client.post(
            reverse("student-setup-profile"),
            {"education_stage": stage.id, "grade_level": "الثالث الثانوي", "daily_study_hours": 3},
            format="json",
        )

    def _project_subjects(self, user=None):
        return sorted(
            Project.objects.filter(owner=user or self.learner).values_list("subject__name", flat=True)
        )

    def test_the_baccalaureate_stage_carries_its_default_subjects(self):
        names = set(self.Subject.objects.filter(education_stage=self.stage).values_list("name", flat=True))
        for name in ("العلوم", "اللغة العربية", "الفيزياء", "الكيمياء", "التاريخ", "الجغرافيا",
                     "الرياضيات - الجبر", "الرياضيات - الهندسة"):
            self.assertIn(name, names)

    def test_choosing_baccalaureate_creates_a_project_per_subject(self):
        response = self._setup_profile(self.stage)
        self.assertIn(response.status_code, (200, 201), response.data)
        expected = sorted(
            self.Subject.objects.filter(education_stage=self.stage, is_active=True).values_list("name", flat=True)
        )
        self.assertEqual(self._project_subjects(), expected)
        project = Project.objects.filter(owner=self.learner).first()
        self.assertEqual(project.title, project.subject.name)

    def test_saving_the_profile_again_creates_no_duplicates_and_respects_deletions(self):
        self._setup_profile(self.stage)
        count = Project.objects.filter(owner=self.learner).count()
        deleted = Project.objects.filter(owner=self.learner).first()
        deleted.delete()  # soft delete by the learner

        self.client.patch(reverse("student-profile"), {"daily_study_hours": 4}, format="json")

        self.assertEqual(Project.all_objects.filter(owner=self.learner).count(), count)
        self.assertEqual(Project.objects.filter(owner=self.learner).count(), count - 1)

    def test_a_subject_added_from_the_dashboard_reaches_learners_already_in_the_stage(self):
        self._setup_profile(self.stage)
        other = self.User.objects.create_user(email="other@example.com", password="StrongPass123!", full_name="x")

        new_subject = self.Subject.objects.create(name="اللغة الإنكليزية", education_stage=self.stage)

        self.assertTrue(Project.objects.filter(owner=self.learner, subject=new_subject).exists())
        self.assertFalse(Project.objects.filter(owner=other).exists())

    def test_inactive_subjects_get_no_project(self):
        self.Subject.objects.create(name="مادة معطلة", education_stage=self.stage, is_active=False)
        self._setup_profile(self.stage)
        self.assertNotIn("مادة معطلة", self._project_subjects())
