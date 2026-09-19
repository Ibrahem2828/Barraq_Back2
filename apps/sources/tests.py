import shutil
import tempfile
from unittest import mock

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APITestCase

from apps.ai_integration.models import AIJob
from apps.projects.models import Project
from apps.subjects.models import EducationStage, Subject
from apps.subscriptions.services import ensure_default_plans, get_or_create_user_subscription

from .capabilities import get_source_character_capabilities
from .models import StudentSource, StudentSourceCollection, StudentSourceInteraction
from .services import process_source, use_source_with_character
from .tasks import process_source_task
from .validators import ALLOWED_EXTENSIONS

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class StudentSourceAPITestCase(APITestCase):
    def setUp(self):
        cache.clear()
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(
            MEDIA_ROOT=self.media_root,
            STUDENT_SOURCE_MAX_UPLOAD_MB=1,
        )
        self.override.enable()

        self.user = User.objects.create_user(
            email='student1@example.com',
            password='StrongPass123',
            full_name='Student One',
        )
        self.other_user = User.objects.create_user(
            email='student2@example.com',
            password='StrongPass123',
            full_name='Student Two',
        )
        self.stage = EducationStage.objects.create(
            name='Secondary',
            description='Secondary stage',
            order=1,
        )
        self.subject = Subject.objects.create(
            name='Mathematics',
            education_stage=self.stage,
            grade_level='Grade 12',
            description='Core subject',
        )
        # Blueprint 01_BACKEND.md §3.1: every source/collection now requires
        # a project.
        self.project = Project.objects.create(owner=self.user, title='Math Project')

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def authenticate(self, user=None):
        self.client.force_authenticate(user or self.user)

    def upload_source(
        self,
        filename='summary.txt',
        content=None,
        content_type='text/plain',
        subject=True,
        collection=None,
        title='Math Summary',
        project=True,
    ):
        self.authenticate()
        file_content = content if content is not None else b'Derivatives help measure change.\nLimits describe behavior near a value.'
        payload = {
            'title': title,
            'description': 'Uploaded class notes',
            'file': SimpleUploadedFile(filename, file_content, content_type=content_type),
        }
        if subject:
            payload['subject'] = self.subject.id
        if collection:
            payload['collection'] = collection.id
        if project:
            payload['project'] = str(self.project.public_id)
        return self.client.post(reverse('student-source-list'), payload, format='multipart')

    def create_collection(self, user=None, subject=True, name='Mathematics', project=True):
        return StudentSourceCollection.objects.create(
            user=user or self.user,
            project=self.project if project else None,
            subject=self.subject if subject else None,
            name=name,
            description='Student folder',
        )

    def test_upload_allowed_file(self):
        response = self.upload_source()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['source_type'], StudentSource.SourceType.TEXT)
        self.assertEqual(StudentSource.objects.filter(user=self.user).count(), 1)

    def test_upload_pdf_file(self):
        response = self.upload_source(
            filename='lesson.pdf',
            content=b'%PDF-1.4\n%test\n',
            content_type='application/pdf',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['source_type'], StudentSource.SourceType.PDF)

    def test_upload_rejects_formats_the_ai_cannot_extract(self):
        """Accepting an upload is a promise to process it.

        Images have no OCR path and legacy OLE Office has no reader in
        Baraq_AI's DocumentExtractor, so these uploads used to succeed and
        then fail at job time with `unsupported_source_format`. They are now
        refused at the boundary, with the supported list in the message.
        """
        unsupported = [
            ('note.png', b'\x89PNG\r\n\x1a\n', 'image/png'),
            ('photo.jpg', b'\xff\xd8\xff\xe0', 'image/jpeg'),
            ('art.webp', b'RIFF\x00\x00\x00\x00WEBP', 'image/webp'),
            ('old.doc', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', 'application/msword'),
            ('old.ppt', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', 'application/vnd.ms-powerpoint'),
        ]
        for filename, content, content_type in unsupported:
            with self.subTest(filename=filename):
                response = self.upload_source(
                    filename=filename, content=content, content_type=content_type
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('PDF', str(response.data['errors']['file']))
        self.assertEqual(StudentSource.objects.count(), 0)

    def test_upload_accepts_every_format_the_ai_can_process(self):
        """The mirror of the test above: nothing the AI supports is refused."""
        supported = [
            ('notes.txt', b'Limits describe behavior near a value.', 'text/plain'),
            (
                'lesson.pdf',
                b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n',
                'application/pdf',
            ),
            ('talk.mp3', b'ID3' + b'\x00' * 40, 'audio/mpeg'),
            ('talk.wav', b'RIFF\x00\x00\x00\x00WAVE' + b'\x00' * 20, 'audio/wav'),
            ('talk.m4a', b'\x00\x00\x00\x20ftypM4A ' + b'\x00' * 20, 'audio/mp4'),
        ]
        for filename, content, content_type in supported:
            with self.subTest(filename=filename):
                response = self.upload_source(
                    filename=filename, content=content, content_type=content_type
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_capabilities_withheld_for_a_legacy_unextractable_source(self):
        """Rows created before the allowlist narrowed still carry source_type
        `image`/`other`. They must not advertise characters whose jobs would
        fail -- this is what covers already-uploaded production data."""
        upload = self.upload_source()
        source = StudentSource.objects.get(pk=upload.data['id'])
        StudentSource.objects.filter(pk=source.pk).update(
            source_type=StudentSource.SourceType.IMAGE
        )

        response = self.client.get(reverse('student-source-capabilities', args=[source.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for character in ('khota', 'fahes', 'rasheed', 'kholasa', 'sada'):
            self.assertFalse(response.data[character]['available'], character)
        self.assertIn('غير مدعوم', response.data['fahes']['message'])

    def test_backend_allowlist_never_exceeds_what_the_ai_can_process(self):
        """Contract guard between two repositories.

        Baraq_AI's DocumentExtractor dispatches on txt/md/csv/json, pdf, docx
        and pptx; the Sada pipeline handles audio/*. Anything this backend
        accepts must fall in one of those buckets, or the upload is a promise
        the platform cannot keep.
        """
        ai_extractable = {'pdf', 'txt', 'md', 'csv', 'json', 'docx', 'pptx'}
        ai_transcribable = {'mp3', 'm4a', 'wav'}
        self.assertEqual(
            ALLOWED_EXTENSIONS - ai_extractable - ai_transcribable,
            set(),
            'backend accepts an extension no AI pipeline can handle',
        )

    def test_upload_arabic_filename(self):
        response = self.upload_source(filename='ملخص الرياضيات.txt')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        source = StudentSource.objects.get(id=response.data['id'])
        self.assertEqual(source.original_filename, 'ملخص الرياضيات.txt')
        self.assertNotIn('ملخص', source.file.name)

    def test_reject_missing_extension(self):
        response = self.upload_source(filename='README', content=b'text')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StudentSource.objects.count(), 0)

    def test_reject_bad_extension(self):
        response = self.upload_source(filename='hack.exe', content=b'bad')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StudentSource.objects.count(), 0)

    def test_reject_missing_file(self):
        self.authenticate()
        response = self.client.post(
            reverse('student-source-list'),
            {'title': 'No File'},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StudentSource.objects.count(), 0)

    def test_reject_missing_title(self):
        self.authenticate()
        response = self.client.post(
            reverse('student-source-list'),
            {'file': SimpleUploadedFile('summary.txt', b'text', content_type='text/plain')},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StudentSource.objects.count(), 0)

    def test_upload_requires_authentication(self):
        response = self.client.post(
            reverse('student-source-list'),
            {
                'title': 'No Token',
                'file': SimpleUploadedFile('summary.txt', b'text', content_type='text/plain'),
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(StudentSource.objects.count(), 0)

    def test_source_file_url_uses_authenticated_download_endpoint(self):
        response = self.upload_source(content=b'private lesson text')
        download_url = response.data['file_url']
        self.assertIn(
            reverse('student-source-download', args=[response.data['id']]),
            download_url,
        )
        self.assertNotIn('/media/', download_url)

        download = self.client.get(download_url)
        self.assertEqual(download.status_code, status.HTTP_200_OK)
        self.assertEqual(download['Cache-Control'], 'private, no-store')
        self.assertEqual(b''.join(download.streaming_content), b'private lesson text')

    def test_source_download_enforces_owner_and_authentication(self):
        response = self.upload_source(content=b'private lesson text')
        url = reverse('student-source-download', args=[response.data['id']])

        self.client.force_authenticate(user=self.other_user)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_upload_without_subject_succeeds(self):
        response = self.upload_source(subject=False)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data['subject'])

    def test_reject_too_large_file(self):
        """Oversize uploads are one rejection with one machine-readable code.

        This used to answer 400 with a codeless `{"file": "..."}` when the
        platform cap tripped, and 403 `file_size_limit_exceeded` when the plan
        cap tripped -- two shapes for one user-visible event, which a client
        could not branch on. Both ceilings now resolve through
        `effective_max_file_size_mb`, so the answer is always the 403 already
        used by every other plan limit (source/storage/collection).
        """
        response = self.upload_source(content=b'a' * (1024 * 1024 + 1))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        # NOTE: the domain code lands under `errors`, while the envelope's
        # top-level `code` is the generic `permission_denied` for every 403.
        # That is pre-existing behaviour of custom_exception_handler and it
        # currently defeats the web client, which reads the top-level code --
        # tracked as a P1-H (error-model) defect, deliberately not changed
        # here because it would alter every error response in the API.
        self.assertEqual(response.data['errors']['code'], 'file_size_limit_exceeded')
        self.assertEqual(int(response.data['errors']['limit']), 1)
        self.assertEqual(StudentSource.objects.count(), 0)

    def test_list_only_own_sources(self):
        own_response = self.upload_source()
        self.assertEqual(own_response.status_code, status.HTTP_201_CREATED)
        StudentSource.objects.create(
            user=self.other_user,
            subject=self.subject,
            title='Other Source',
            source_type=StudentSource.SourceType.TEXT,
            file='student_sources/other.txt',
            original_filename='other.txt',
            file_size=10,
            mime_type='text/plain',
            extension='txt',
        )

        response = self.client.get(reverse('student-source-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if isinstance(response.data, dict) else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Math Summary')

    def test_cannot_access_other_user_source(self):
        other_source = StudentSource.objects.create(
            user=self.other_user,
            subject=self.subject,
            title='Other Source',
            source_type=StudentSource.SourceType.TEXT,
            file='student_sources/other.txt',
            original_filename='other.txt',
            file_size=10,
            mime_type='text/plain',
            extension='txt',
        )
        self.authenticate()

        response = self.client.get(reverse('student-source-detail', args=[other_source.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_process_txt(self):
        upload = self.upload_source()
        source = StudentSource.objects.get(pk=upload.data['id'])

        response = self.client.post(reverse('student-source-process', args=[source.id]))
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        result = process_source(source)
        source.refresh_from_db()
        self.assertTrue(result['success'])
        self.assertEqual(source.status, StudentSource.Status.READY)
        self.assertTrue(source.extracted_text)

    def test_process_returns_queued_envelope_not_a_bare_source(self):
        """Contract guard for SourceProcessingQueuedResponseSerializer.

        The action answers 202 with {message, source} -- not a bare
        StudentSourceDetail. Clients typed against the bare serializer read
        `undefined` for every field, so this shape is part of the published
        contract and must not silently regress.
        """
        upload = self.upload_source()
        source = StudentSource.objects.get(pk=upload.data['id'])

        response = self.client.post(reverse('student-source-process', args=[source.id]))

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(set(response.data), {'message', 'source'})
        self.assertTrue(response.data['message'])
        self.assertEqual(response.data['source']['id'], source.id)
        self.assertIn('status', response.data['source'])
        self.assertIn('capabilities', response.data['source'])

    def test_process_while_already_processing_uses_the_same_envelope(self):
        upload = self.upload_source()
        source = StudentSource.objects.get(pk=upload.data['id'])
        StudentSource.objects.filter(pk=source.pk).update(
            status=StudentSource.Status.PROCESSING
        )

        response = self.client.post(reverse('student-source-process', args=[source.id]))

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(set(response.data), {'message', 'source'})
        self.assertEqual(response.data['source']['id'], source.id)
        self.assertEqual(
            response.data['source']['status'], StudentSource.Status.PROCESSING
        )

    def test_source_capabilities(self):
        upload = self.upload_source()

        response = self.client.get(reverse('student-source-capabilities', args=[upload.data['id']]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['khota']['available'])
        self.assertFalse(response.data['kholasa']['available'])

    def test_capabilities_available_for_an_uploaded_non_text_source(self):
        """UPLOADED is a terminal success state for non-text sources.

        process_source() deliberately leaves every non-text source there
        (extraction belongs to the AI service), and use_source_with_character
        accepts it -- so capabilities must advertise the content characters
        rather than implying the source is still being worked on.
        """
        upload = self.upload_source(
            filename='slides.pdf',
            content=b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n',
            content_type='application/pdf',
        )
        source = StudentSource.objects.get(pk=upload.data['id'])
        process_source(source)
        source.refresh_from_db()
        self.assertEqual(source.status, StudentSource.Status.UPLOADED)

        response = self.client.get(reverse('student-source-capabilities', args=[source.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['khota']['available'])
        self.assertTrue(response.data['fahes']['available'])

    def test_capabilities_withheld_for_a_failed_source(self):
        """A failed source must not advertise actions the request layer rejects.

        use_source_with_character raises for a failed source regardless of
        character -- Rasheed included -- so every entry must be unavailable
        and carry a reason the UI can show.
        """
        upload = self.upload_source()
        source = StudentSource.objects.get(pk=upload.data['id'])
        StudentSource.objects.filter(pk=source.pk).update(status=StudentSource.Status.FAILED)

        response = self.client.get(reverse('student-source-capabilities', args=[source.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for character in ('khota', 'fahes', 'rasheed', 'kholasa', 'sada'):
            self.assertFalse(response.data[character]['available'], character)
            self.assertEqual(response.data[character]['actions'], [], character)
            self.assertTrue(response.data[character]['message'], character)

    def test_capabilities_match_what_use_with_character_actually_accepts(self):
        """The contract guard: capabilities must never disagree with the
        request handler it describes, for any reachable status."""
        upload = self.upload_source()
        source = StudentSource.objects.get(pk=upload.data['id'])

        for source_status in StudentSource.Status.values:
            StudentSource.objects.filter(pk=source.pk).update(status=source_status)
            source.refresh_from_db()
            advertised = get_source_character_capabilities(source)['rasheed']['available']
            try:
                use_source_with_character(
                    self.user, source, StudentSourceInteraction.Character.RASHEED
                )
                accepted = True
            except DRFValidationError:
                accepted = False
            self.assertEqual(
                advertised,
                accepted,
                f'capabilities and use_source_with_character disagree for status={source_status}',
            )

    def test_capabilities_keep_plan_reason_for_a_gated_character(self):
        """Source state must not mask a subscription reason, or an upsell
        prompt would be replaced by a misleading 'not ready' message."""
        upload = self.upload_source()
        source = StudentSource.objects.get(pk=upload.data['id'])

        capabilities = get_source_character_capabilities(
            source, features={'can_use_khota': True, 'can_use_kholasa': False}
        )

        self.assertTrue(capabilities['khota']['available'])
        self.assertFalse(capabilities['kholasa']['available'])
        self.assertIn('خطتك', capabilities['kholasa']['message'])

    def test_use_with_rasheed(self):
        upload = self.upload_source()

        response = self.client.post(
            reverse('student-source-use-with-character', args=[upload.data['id']]),
            {'character': StudentSourceInteraction.Character.RASHEED},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertIn('ai_job', response.data)
        self.assertEqual(response.data['ai_job']['task_type'], AIJob.TaskType.RASHEED_RECOMMENDATIONS)

    def test_use_with_khota_does_not_fail(self):
        upload = self.upload_source()

        response = self.client.post(reverse('student-source-use-with-khota', args=[upload.data['id']]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['ai_job']['task_type'], AIJob.TaskType.KHOTA_GENERATE_PLAN)

    def test_use_with_fahes_does_not_fail(self):
        upload = self.upload_source()
        self.client.post(reverse('student-source-process', args=[upload.data['id']]))

        response = self.client.post(reverse('student-source-use-with-fahes', args=[upload.data['id']]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['ai_job']['task_type'], AIJob.TaskType.FAHES_GENERATE_QUIZ)

    def test_kholasa_unavailable(self):
        upload = self.upload_source()

        response = self.client.post(reverse('student-source-use-with-kholasa', args=[upload.data['id']]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_sada_unavailable(self):
        upload = self.upload_source()

        response = self.client.post(reverse('student-source-use-with-sada', args=[upload.data['id']]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_source(self):
        upload = self.upload_source()

        response = self.client.delete(reverse('student-source-detail', args=[upload.data['id']]))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(StudentSource.objects.filter(user=self.user).count(), 0)

    def test_create_collection(self):
        self.authenticate()
        response = self.client.post(
            reverse('student-source-collection-list'),
            {
                'name': 'الرياضيات',
                'subject': self.subject.id,
                'description': 'مصادر الرياضيات',
                'project': str(self.project.public_id),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['name'], 'الرياضيات')
        self.assertEqual(StudentSourceCollection.objects.filter(user=self.user).count(), 1)

    def test_list_only_own_collections(self):
        own_collection = self.create_collection(name='Own Folder')
        self.create_collection(user=self.other_user, name='Other Folder')
        self.authenticate()

        response = self.client.get(reverse('student-source-collection-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if isinstance(response.data, dict) else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], own_collection.id)

    def test_cannot_access_other_user_collection(self):
        other_collection = self.create_collection(user=self.other_user, name='Other Folder')
        self.authenticate()

        response = self.client.get(reverse('student-source-collection-detail', args=[other_collection.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_upload_source_inside_collection(self):
        collection = self.create_collection(name='Math Folder')
        response = self.upload_source(collection=collection, subject=False)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        source = StudentSource.objects.get(id=response.data['id'])
        self.assertEqual(source.collection_id, collection.id)
        self.assertEqual(source.subject_id, self.subject.id)

    def test_collection_sources_endpoint(self):
        collection = self.create_collection(name='Math Folder')
        upload = self.upload_source(collection=collection)

        response = self.client.get(reverse('student-source-collection-sources', args=[collection.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], upload.data['id'])

    def test_collection_capabilities(self):
        collection = self.create_collection(name='Math Folder')
        self.upload_source(collection=collection)

        response = self.client.get(reverse('student-source-collection-capabilities', args=[collection.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['khota']['available'])
        self.assertFalse(response.data['kholasa']['available'])

    def test_collection_capabilities_withheld_when_every_source_failed(self):
        """use_collection_with_character requires one *usable* source, so a
        folder holding only failed sources must not advertise actions."""
        collection = self.create_collection(name='Math Folder')
        upload = self.upload_source(collection=collection)
        StudentSource.objects.filter(pk=upload.data['id']).update(
            status=StudentSource.Status.FAILED
        )

        response = self.client.get(
            reverse('student-source-collection-capabilities', args=[collection.id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['khota']['available'])
        self.assertFalse(response.data['fahes']['available'])
        # Distinct from the empty-folder message: the folder is not empty.
        self.assertNotIn('أضف مصادر', response.data['khota']['message'])

    def test_use_collection_with_rasheed(self):
        collection = self.create_collection(name='Math Folder')
        self.upload_source(collection=collection)

        response = self.client.post(reverse('student-source-collection-use-with-rasheed', args=[collection.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertIn('ai_job', response.data)
        self.assertEqual(response.data['ai_job']['task_type'], AIJob.TaskType.RASHEED_RECOMMENDATIONS)

    def test_use_collection_with_khota_does_not_500(self):
        collection = self.create_collection(name='Math Folder')
        self.upload_source(collection=collection)

        response = self.client.post(reverse('student-source-collection-use-with-khota', args=[collection.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['ai_job']['task_type'], AIJob.TaskType.KHOTA_GENERATE_PLAN)

    def test_use_collection_with_fahes_does_not_500(self):
        collection = self.create_collection(name='Math Folder')
        upload = self.upload_source(collection=collection)
        self.client.post(reverse('student-source-process', args=[upload.data['id']]))

        response = self.client.post(reverse('student-source-collection-use-with-fahes', args=[collection.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['ai_job']['task_type'], AIJob.TaskType.FAHES_GENERATE_QUIZ)

    def test_collection_kholasa_unavailable(self):
        collection = self.create_collection(name='Math Folder')
        self.authenticate()

        response = self.client.post(reverse('student-source-collection-use-with-kholasa', args=[collection.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_collection_sada_unavailable(self):
        collection = self.create_collection(name='Math Folder')
        self.authenticate()

        response = self.client.post(reverse('student-source-collection-use-with-sada', args=[collection.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_collection_with_sources_returns_400(self):
        collection = self.create_collection(name='Math Folder')
        self.upload_source(collection=collection)

        response = self.client.delete(reverse('student-source-collection-detail', args=[collection.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(StudentSourceCollection.objects.filter(id=collection.id).exists())


class SourceProcessingRetryTests(APITestCase):
    """Celery retry correctness for source processing.

    ``process_source_task`` declares ``autoretry_for=(OSError,)``, but
    ``process_source`` used to catch every exception and *return* a failure
    dict. A swallowed exception never reaches Celery, so autoretry could
    never fire and a transient storage blip permanently marked the source
    FAILED on the first attempt.
    """

    def setUp(self):
        cache.clear()
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.user = User.objects.create_user(
            email='retry@example.com', password='StrongPass123', full_name='Retry User'
        )
        self.project = Project.objects.create(owner=self.user, title='Retry Project')
        self.source = StudentSource.objects.create(
            user=self.user,
            project=self.project,
            title='Notes',
            original_filename='notes.txt',
            file_size=4,
            mime_type='text/plain',
            extension='txt',
            source_type=StudentSource.SourceType.TEXT,
        )
        self.source.file.save('notes.txt', ContentFile(b'text'), save=True)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_a_retriable_storage_error_propagates_for_celery_to_retry(self):
        with mock.patch(
            'apps.sources.services._sha256_file', side_effect=OSError('storage unavailable')
        ), self.assertRaises(OSError):
            process_source(self.source)

        self.source.refresh_from_db()
        # The atomic block rolled back: not prematurely FAILED, so the retry
        # that may well succeed still has a source to work on.
        self.assertEqual(self.source.status, StudentSource.Status.UPLOADED)

    def test_the_task_retries_a_storage_error_rather_than_failing_the_source(self):
        with (
            mock.patch(
                'apps.sources.services._sha256_file', side_effect=OSError('storage unavailable')
            ),
            self.assertRaises(OSError),
        ):
            process_source_task.push_request(retries=0)
            try:
                process_source_task(self.source.pk)
            finally:
                process_source_task.pop_request()

        self.source.refresh_from_db()
        self.assertEqual(self.source.status, StudentSource.Status.UPLOADED)

    def test_the_task_records_failure_once_the_retries_are_spent(self):
        with (
            mock.patch(
                'apps.sources.services._sha256_file', side_effect=OSError('storage unavailable')
            ),
            self.assertRaises(OSError),
        ):
            process_source_task.push_request(retries=process_source_task.max_retries)
            try:
                process_source_task(self.source.pk)
            finally:
                process_source_task.pop_request()

        self.source.refresh_from_db()
        self.assertEqual(self.source.status, StudentSource.Status.FAILED)
        self.assertTrue(self.source.processing_error)

    def test_a_permanent_error_fails_immediately_without_retrying(self):
        """A file we cannot decode will never decode. Retrying it three times
        only delays the user's feedback."""
        with mock.patch(
            'apps.sources.services._read_text_file',
            side_effect=DRFValidationError('undecodable'),
        ):
            result = process_source(self.source)

        self.assertFalse(result['success'])
        self.assertEqual(result['code'], 'source_processing_failed')
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, StudentSource.Status.FAILED)

    def test_otp_and_email_tasks_reraise_so_celery_can_retry(self):
        """The same class of defect, checked on the OTP path.

        `send_email_otp` must not convert an SMTP failure into a silent
        success -- a student would simply never receive the code.
        """
        from apps.users.tasks import send_email_otp

        with (
            mock.patch('apps.users.tasks.send_mail', side_effect=OSError('smtp down')),
            mock.patch.object(send_email_otp, 'retry', side_effect=Retry()) as retry,
            self.assertRaises(Retry),
        ):
            send_email_otp('student@example.com', '123456')

        self.assertEqual(retry.call_count, 1)


class CapabilityCoherenceTests(APITestCase):
    """One answer to "can this user use character X with source Y".

    The list and detail serializers resolved capabilities without the
    subscription, so a Free user's source list advertised Kholasa and Sada
    while /capabilities/ -- the endpoint written for exactly that question --
    said the opposite on the same page load.
    """

    def setUp(self):
        cache.clear()
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.user = User.objects.create_user(
            email='caps@example.com', password='StrongPass123', full_name='Caps'
        )
        self.stage = EducationStage.objects.create(name='Secondary', description='s', order=1)
        self.subject = Subject.objects.create(
            name='Maths', education_stage=self.stage, grade_level='12', description='d'
        )
        self.project = Project.objects.create(owner=self.user, title='Caps Project')
        ensure_default_plans()
        get_or_create_user_subscription(self.user)
        self.source = StudentSource.objects.create(
            user=self.user,
            project=self.project,
            subject=self.subject,
            title='Notes',
            source_type=StudentSource.SourceType.TEXT,
            file=SimpleUploadedFile('n.txt', b'text', content_type='text/plain'),
            original_filename='n.txt',
            file_size=4,
            mime_type='text/plain',
            extension='txt',
            status=StudentSource.Status.READY,
        )
        self.client.force_authenticate(self.user)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _endpoint_caps(self):
        return self.client.get(
            reverse('student-source-capabilities', args=[self.source.id])
        ).data

    def test_the_list_agrees_with_the_capabilities_endpoint(self):
        listed = self.client.get(reverse('student-source-list')).data['results'][0]
        endpoint = self._endpoint_caps()

        for character in ('khota', 'fahes', 'rasheed', 'kholasa', 'sada'):
            with self.subTest(character=character):
                self.assertEqual(
                    listed['capabilities'][character]['available'],
                    endpoint[character]['available'],
                    f'list and /capabilities/ disagree about {character}',
                )

    def test_the_detail_agrees_with_the_capabilities_endpoint(self):
        detail = self.client.get(
            reverse('student-source-detail', args=[self.source.id])
        ).data
        endpoint = self._endpoint_caps()

        for character in ('khota', 'fahes', 'rasheed', 'kholasa', 'sada'):
            with self.subTest(character=character):
                self.assertEqual(
                    detail['capabilities'][character]['available'],
                    endpoint[character]['available'],
                )

    def test_a_plan_gated_character_is_withheld_everywhere(self):
        """Free plan excludes Kholasa and Sada. No surface may offer them."""
        listed = self.client.get(reverse('student-source-list')).data['results'][0]
        detail = self.client.get(
            reverse('student-source-detail', args=[self.source.id])
        ).data

        for payload in (listed['capabilities'], detail['capabilities'], self._endpoint_caps()):
            self.assertFalse(payload['kholasa']['available'])
            self.assertFalse(payload['sada']['available'])
        # ...and the request layer refuses it too, which is what actually
        # protects the entitlement.
        response = self.client.post(
            reverse('student-source-use-with-kholasa', args=[self.source.id])
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_collection_capabilities_also_respect_the_plan(self):
        collection = StudentSourceCollection.objects.create(
            user=self.user, project=self.project, name='Folder'
        )
        self.source.collection = collection
        self.source.save(update_fields=['collection'])

        detail = self.client.get(
            reverse('student-source-collection-detail', args=[collection.id])
        ).data

        self.assertFalse(detail['capabilities']['kholasa']['available'])
        self.assertFalse(detail['characters_summary']['kholasa']['available'])
