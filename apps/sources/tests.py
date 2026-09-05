import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.ai_integration.models import AIJob
from apps.subjects.models import EducationStage, Subject

from .models import StudentSource, StudentSourceCollection, StudentSourceInteraction
from .services import process_source

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class StudentSourceAPITestCase(APITestCase):
    def setUp(self):
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
        return self.client.post(reverse('student-source-list'), payload, format='multipart')

    def create_collection(self, user=None, subject=True, name='Mathematics'):
        return StudentSourceCollection.objects.create(
            user=user or self.user,
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

    def test_upload_png_file(self):
        response = self.upload_source(
            filename='note.png',
            content=b'\x89PNG\r\n\x1a\n',
            content_type='image/png',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['source_type'], StudentSource.SourceType.IMAGE)

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

    def test_upload_without_subject_succeeds(self):
        response = self.upload_source(subject=False)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data['subject'])

    def test_reject_too_large_file(self):
        response = self.upload_source(content=b'a' * (1024 * 1024 + 1))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
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

    def test_source_capabilities(self):
        upload = self.upload_source()

        response = self.client.get(reverse('student-source-capabilities', args=[upload.data['id']]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['khota']['available'])
        self.assertFalse(response.data['kholasa']['available'])

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
            {'name': 'الرياضيات', 'subject': self.subject.id, 'description': 'مصادر الرياضيات'},
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
