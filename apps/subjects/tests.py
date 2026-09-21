import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
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
    demo_passwords = {
        'BARAQ_DEMO_ADMIN_PASSWORD': 'test-admin-password',
        'BARAQ_DEMO_PROJECT_ADMIN_PASSWORD': 'test-project-admin-password',
        'BARAQ_DEMO_STUDENT_PASSWORD': 'test-student-password',
    }

    def test_command_requires_explicit_opt_in_and_environment_credentials(self):
        with self.assertRaisesMessage(CommandError, '--allow-demo-data'):
            call_command('seed_demo_data', stdout=StringIO())

        with patch.dict(os.environ, {
            'BARAQ_DEMO_ADMIN_PASSWORD': '',
            'BARAQ_DEMO_PROJECT_ADMIN_PASSWORD': '',
            'BARAQ_DEMO_STUDENT_PASSWORD': '',
        }, clear=False), self.assertRaisesMessage(CommandError, 'Demo credentials were not supplied'):
            call_command('seed_demo_data', allow_demo_data=True, stdout=StringIO())

    def test_seeded_sources_and_ai_jobs_are_project_scoped(self):
        """A fresh demo database must exercise the current project contract."""

        output = StringIO()
        with patch.dict(os.environ, self.demo_passwords, clear=False):
            call_command('seed_demo_data', allow_demo_data=True, stdout=output)

        student = User.objects.get(email='student@baraq.app')
        project = Project.objects.get(owner=student, title='مشروع مراجعة الرياضيات')

        self.assertTrue(StudentSource.objects.filter(user=student, project=project).exists())
        self.assertTrue(StudentSourceCollection.objects.filter(user=student, project=project).exists())
        jobs = AIJob.objects.filter(user=student)
        self.assertGreater(jobs.count(), 0)
        self.assertFalse(jobs.filter(project__isnull=True).exists())
        self.assertTrue(student.check_password(self.demo_passwords['BARAQ_DEMO_STUDENT_PASSWORD']))
        self.assertNotIn(self.demo_passwords['BARAQ_DEMO_STUDENT_PASSWORD'], output.getvalue())
