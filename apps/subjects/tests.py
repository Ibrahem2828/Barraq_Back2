from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.ai_integration.models import AIJob
from apps.projects.models import Project
from apps.sources.models import StudentSource, StudentSourceCollection

from .models import EducationStage, Subject, UserSubject

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class SubjectApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='subjects@example.com', password='StrongPass123!', full_name='Subject User')
        self.other = User.objects.create_user(email='other-subjects@example.com', password='StrongPass123!', full_name='Other')
        self.stage = EducationStage.objects.create(name='Secondary', order=1)
        self.subject = Subject.objects.create(name='Mathematics', education_stage=self.stage, grade_level='12')

    def test_public_catalog_is_available(self):
        self.assertEqual(self.client.get(reverse('education-stages')).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(reverse('subjects')).status_code, status.HTTP_200_OK)

    def test_user_subjects_are_isolated_and_unique(self):
        UserSubject.objects.create(user=self.other, subject=self.subject)
        self.client.force_authenticate(user=self.user)
        create = self.client.post(reverse('user-subjects'), {'subject': self.subject.id}, format='json')
        self.assertEqual(create.status_code, status.HTTP_201_CREATED)
        duplicate = self.client.post(reverse('user-subjects'), {'subject': self.subject.id}, format='json')
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.get(reverse('user-subjects'))
        self.assertEqual(response.data['count'], 1)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'], AI_SERVICE_ENABLED=False)
class DemoSeedCommandTests(TestCase):
    def test_seeded_sources_and_ai_jobs_are_project_scoped(self):
        """A fresh demo database must exercise the current project contract."""

        call_command('seed_demo_data', stdout=StringIO())

        student = User.objects.get(email='student@baraq.app')
        project = Project.objects.get(owner=student, title='مشروع مراجعة الرياضيات')

        self.assertTrue(StudentSource.objects.filter(user=student, project=project).exists())
        self.assertTrue(StudentSourceCollection.objects.filter(user=student, project=project).exists())
        jobs = AIJob.objects.filter(user=student)
        self.assertGreater(jobs.count(), 0)
        self.assertFalse(jobs.filter(project__isnull=True).exists())
