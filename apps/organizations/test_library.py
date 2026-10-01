import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.admin_dashboard.models import AuditLog
from apps.admin_dashboard.services import assign_roles_to_user, seed_default_rbac
from apps.notifications.models import Notification
from apps.projects.models import Project
from apps.sources.models import StudentSource
from apps.subjects.models import EducationStage, Subject
from apps.subscriptions.models import SubscriptionUsage

from .models import ClassLibraryItem, ClassMembership, Classroom, Organization, OrganizationMembership

User = get_user_model()
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


def pdf(name="handout.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class ClassLibraryTests(APITestCase):
    """Classroom Shared Library: staff publish, members read and use."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.media = override_settings(MEDIA_ROOT=self.media_root)
        self.media.enable()
        self.task = mock.patch("apps.sources.tasks.process_source_task.delay")
        self.task.start()

        _, self.roles = seed_default_rbac()
        self.super_admin = User.objects.create_superuser(
            email="lib-super@example.com", password="StrongPass123", full_name="Super"
        )
        assign_roles_to_user(self.super_admin, [self.roles["super_admin"]], self.super_admin, scopes=[{"scope_type": "global"}])
        stage = EducationStage.objects.create(name="Secondary", order=1)
        self.physics = Subject.objects.create(name="Physics", education_stage=stage, grade_level="10")

        self.org_a = Organization.objects.create(name="School A", created_by=self.super_admin)
        self.org_b = Organization.objects.create(name="School B", created_by=self.super_admin)
        self.class_a1 = Classroom.objects.create(organization=self.org_a, name="10-A")
        self.class_a2 = Classroom.objects.create(organization=self.org_a, name="10-B")
        self.class_b1 = Classroom.objects.create(organization=self.org_b, name="11-A")

        self.manager_a = self._admin("manager-a@example.com", "organization_manager", {"scope_type": "organization", "organization": self.org_a})
        self.manager_b = self._admin("manager-b@example.com", "organization_manager", {"scope_type": "organization", "organization": self.org_b})
        self.teacher_a1 = self._admin("teacher-a1@example.com", "class_supervisor", {"scope_type": "class", "classroom": self.class_a1})

        self.alice = self._student("alice@example.com", self.org_a, self.class_a1)
        self.bob = self._student("bob@example.com", self.org_a, self.class_a2)
        self.carol = self._student("carol@example.com", self.org_b, self.class_b1)
        self.loner = User.objects.create_user(email="loner@example.com", password="StrongPass123", full_name="Loner")

    def tearDown(self):
        self.task.stop()
        self.media.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    # -- fixtures ---------------------------------------------------------
    def _admin(self, email, role, scope):
        user = User.objects.create_user(email=email, password="StrongPass123", full_name=email, role=User.Roles.ADMIN)
        assign_roles_to_user(user, [self.roles[role]], self.super_admin, scopes=[scope])
        return user

    def _student(self, email, organization, classroom):
        user = User.objects.create_user(email=email, password="StrongPass123", full_name=email.split("@")[0])
        OrganizationMembership.objects.create(organization=organization, user=user, member_type="student", status="active")
        ClassMembership.objects.create(classroom=classroom, user=user, status="active")
        return user

    def upload(self, actor, **fields):
        self.client.force_authenticate(user=actor)
        data = {"title": "Unit 2 handout", "category": "handout", "file": pdf(), **fields}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("admin-class-library-list"), data, format="multipart")

    def item(self, organization=None, classroom=None, subject=None, title="Shared", content=PDF, name="shared.pdf", source_type="pdf"):
        organization = organization or classroom.organization
        item = ClassLibraryItem(
            organization=organization,
            classroom=classroom,
            subject=subject,
            title=title,
            original_filename=name,
            file_size=len(content),
            extension=name[name.rfind("."):],
            source_type=source_type,
            mime_type="application/pdf",
            sha256="x" * 64,
            uploaded_by=self.super_admin,
        )
        item.file = SimpleUploadedFile(name, content)
        item.save()
        return item

    @staticmethod
    def rows(response):
        data = response.data
        return data["results"] if isinstance(data, dict) and "results" in data else data

    # -- staff: publishing --------------------------------------------------
    def test_organization_manager_shares_with_the_whole_organization(self):
        response = self.upload(self.manager_a, organization=str(self.org_a.public_id), subject=self.physics.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        item = ClassLibraryItem.objects.get(public_id=response.data["public_id"])
        self.assertIsNone(item.classroom)
        self.assertEqual(item.source_type, "pdf")
        self.assertEqual(len(item.sha256), 64)
        self.assertEqual(response.data["characters"], ["fahes", "kholasa", "khota"])
        self.assertTrue(AuditLog.objects.filter(action="library.item_uploaded", actor=self.manager_a).exists())
        # Every student of the organization hears about it -- once.
        notified = set(Notification.objects.filter(idempotency_key=f"class-library:{item.public_id}").values_list("user_id", flat=True))
        self.assertEqual(notified, {self.alice.pk, self.bob.pk})

    def test_class_supervisor_shares_with_its_class(self):
        response = self.upload(self.teacher_a1, classroom=str(self.class_a1.public_id))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["classroom_name"], "10-A")
        notified = set(Notification.objects.values_list("user_id", flat=True))
        self.assertEqual(notified, {self.alice.pk})

    def test_class_supervisor_cannot_publish_beyond_its_class(self):
        sibling = self.upload(self.teacher_a1, classroom=str(self.class_a2.public_id))
        whole_school = self.upload(self.teacher_a1, organization=str(self.org_a.public_id))
        other_school = self.upload(self.manager_a, classroom=str(self.class_b1.public_id))

        for response in (sibling, whole_school, other_school):
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertFalse(ClassLibraryItem.objects.exists())

    def test_unsafe_files_are_refused(self):
        script = SimpleUploadedFile("run.exe", b"MZ\x90\x00", content_type="application/octet-stream")
        fake_pdf = SimpleUploadedFile("fake.pdf", b"not a pdf at all", content_type="application/pdf")

        for upload in (script, fake_pdf):
            response = self.upload(self.manager_a, organization=str(self.org_a.public_id), file=upload)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ClassLibraryItem.objects.exists())

    def test_staff_lists_are_scoped(self):
        shared = self.item(organization=self.org_a)
        a1 = self.item(classroom=self.class_a1)
        a2 = self.item(classroom=self.class_a2)
        b1 = self.item(classroom=self.class_b1)

        def listed(actor):
            self.client.force_authenticate(user=actor)
            response = self.client.get(reverse("admin-class-library-list"))
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            return {row["public_id"] for row in self.rows(response)}

        self.assertEqual(listed(self.manager_a), {str(shared.public_id), str(a1.public_id), str(a2.public_id)})
        self.assertEqual(listed(self.teacher_a1), {str(a1.public_id)})
        self.assertEqual(listed(self.manager_b), {str(b1.public_id)})
        self.assertEqual(len(listed(self.super_admin)), 4)

        self.client.force_authenticate(user=self.manager_b)
        response = self.client.get(reverse("admin-class-library-detail", args=[a1.public_id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_role_without_library_permission_is_denied(self):
        finance = self._admin("finance@example.com", "finance", {"scope_type": "global"})
        self.client.force_authenticate(user=finance)

        response = self.client.get(reverse("admin-class-library-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_deleting_an_item_keeps_students_copies(self):
        item = self.item(classroom=self.class_a1)
        self.client.force_authenticate(user=self.alice)
        self.client.post(reverse("class-library-use", args=[item.public_id]), {"character": "fahes"}, format="json")
        copy = StudentSource.objects.get(user=self.alice)

        self.client.force_authenticate(user=self.teacher_a1)
        response = self.client.delete(reverse("admin-class-library-detail", args=[item.public_id]))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ClassLibraryItem.objects.exists())
        copy.refresh_from_db()
        self.assertEqual(copy.file.read(), PDF)

    # -- students -----------------------------------------------------------
    def test_students_see_their_classes_and_their_organization(self):
        shared = self.item(organization=self.org_a)
        a1 = self.item(classroom=self.class_a1)
        self.item(classroom=self.class_a2)
        self.item(classroom=self.class_b1)
        archived = self.item(classroom=self.class_a1)
        archived.status = ClassLibraryItem.Status.ARCHIVED
        archived.save()

        def listed(student):
            self.client.force_authenticate(user=student)
            response = self.client.get(reverse("class-library-list"))
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            return {row["public_id"] for row in self.rows(response)}

        self.assertEqual(listed(self.alice), {str(shared.public_id), str(a1.public_id)})
        self.assertEqual(len(listed(self.bob)), 2)  # shared + 10-B
        self.assertEqual(listed(self.loner), set())

    def test_a_student_leaving_the_class_loses_the_library(self):
        a1 = self.item(classroom=self.class_a1)
        ClassMembership.objects.filter(user=self.alice).update(status="removed")
        self.client.force_authenticate(user=self.alice)

        response = self.client.get(reverse("class-library-detail", args=[a1.public_id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_download_is_members_only(self):
        a1 = self.item(classroom=self.class_a1)

        self.client.force_authenticate(user=self.alice)
        allowed = self.client.get(reverse("class-library-download", args=[a1.public_id]))
        self.client.force_authenticate(user=self.carol)
        denied = self.client.get(reverse("class-library-download", args=[a1.public_id]))

        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(allowed.streaming_content), PDF)
        self.assertEqual(allowed["Cache-Control"], "private, no-store")
        self.assertEqual(denied.status_code, status.HTTP_404_NOT_FOUND)

    def test_using_an_item_copies_it_into_the_subject_project_once(self):
        project = Project.objects.create(owner=self.alice, title="Physics", subject=self.physics)
        item = self.item(classroom=self.class_a1, subject=self.physics)
        usage_before = list(SubscriptionUsage.objects.filter(user=self.alice).values("sources_uploaded", "storage_used_bytes"))
        self.client.force_authenticate(user=self.alice)
        url = reverse("class-library-use", args=[item.public_id])

        first = self.client.post(url, {"character": "fahes"}, format="json")
        again = self.client.post(url, {"character": "kholasa"}, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        self.assertEqual(again.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["source_id"], again.data["source_id"])
        self.assertEqual(str(first.data["project"]), str(project.public_id))
        source = StudentSource.objects.get(pk=first.data["source_id"])
        self.assertEqual(source.user, self.alice)
        self.assertEqual(source.subject, self.physics)
        self.assertEqual(source.source_type, "pdf")
        self.assertEqual(source.metadata["library_item"], str(item.public_id))
        self.assertEqual(source.file.read(), PDF)
        self.assertNotEqual(source.file.name, item.file.name)  # a copy, not a shared path
        # Not an upload: the learner's quota is untouched.
        self.assertEqual(
            list(SubscriptionUsage.objects.filter(user=self.alice).values("sources_uploaded", "storage_used_bytes")),
            usage_before,
        )

    def test_without_a_subject_project_the_copy_goes_to_a_library_project(self):
        item = self.item(classroom=self.class_a1)
        self.client.force_authenticate(user=self.alice)

        response = self.client.post(reverse("class-library-use", args=[item.public_id]), {}, format="json")
        second = self.item(classroom=self.class_a1, title="Another")
        again = self.client.post(reverse("class-library-use", args=[second.public_id]), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["project_title"], "مكتبة 10-A")
        self.assertEqual(str(again.data["project"]), str(response.data["project"]))  # one library project per class

    def test_a_named_project_must_be_the_students_own(self):
        foreign = Project.objects.create(owner=self.bob, title="Bob's")
        item = self.item(classroom=self.class_a1)
        self.client.force_authenticate(user=self.alice)

        response = self.client.post(
            reverse("class-library-use", args=[item.public_id]), {"project": str(foreign.public_id)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(StudentSource.objects.exists())

    def test_character_must_suit_the_file(self):
        recording = self.item(classroom=self.class_a1, content=WAV, name="lesson.wav", source_type="audio")
        handout = self.item(classroom=self.class_a1)
        self.client.force_authenticate(user=self.alice)

        audio_to_fahes = self.client.post(reverse("class-library-use", args=[recording.public_id]), {"character": "fahes"}, format="json")
        pdf_to_sada = self.client.post(reverse("class-library-use", args=[handout.public_id]), {"character": "sada"}, format="json")
        audio_to_sada = self.client.post(reverse("class-library-use", args=[recording.public_id]), {"character": "sada"}, format="json")

        self.assertEqual(audio_to_fahes.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(pdf_to_sada.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(audio_to_sada.status_code, status.HTTP_201_CREATED, audio_to_sada.data)

    def test_outsiders_cannot_use_an_item(self):
        a1 = self.item(classroom=self.class_a1)
        self.client.force_authenticate(user=self.carol)

        response = self.client.post(reverse("class-library-use", args=[a1.public_id]), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(StudentSource.objects.exists())
