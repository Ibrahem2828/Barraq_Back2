from datetime import timedelta

import environ
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.common.env_config import (
    resolve_list_setting,
    validate_allowed_hosts,
    validate_origin_url,
    validate_secret_key,
)
from apps.quizzes.models import Quiz
from apps.study_plans.models import StudyPlan
from apps.subjects.models import EducationStage, Subject

User = get_user_model()


def _env_from(mapping):
    env = environ.Env()
    env.ENVIRON = dict(mapping)
    return env


class ResolveListSettingTests(SimpleTestCase):
    """Guards against the CSRF_TRUSTED_ORIGINS / DJANGO_CSRF_TRUSTED_ORIGINS
    naming conflict silently dropping real production domains."""

    def test_reads_primary_key_when_only_it_is_set(self):
        env = _env_from({
            'CSRF_TRUSTED_ORIGINS': 'https://api.barraq.example,https://dashboard.barraq.example',
        })

        result = resolve_list_setting(env, 'CSRF_TRUSTED_ORIGINS', fallback_keys=('DJANGO_CSRF_TRUSTED_ORIGINS',))

        self.assertEqual(
            result,
            ['https://api.barraq.example', 'https://dashboard.barraq.example'],
        )

    def test_raises_when_only_a_legacy_fallback_key_is_set(self):
        env = _env_from({
            'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://api.barraq.example,https://dashboard.barraq.example',
        })

        with self.assertRaises(ValueError):
            resolve_list_setting(env, 'CSRF_TRUSTED_ORIGINS', fallback_keys=('DJANGO_CSRF_TRUSTED_ORIGINS',))

    def test_raises_when_both_primary_and_legacy_key_are_set(self):
        env = _env_from({
            'CSRF_TRUSTED_ORIGINS': 'http://localhost:3000',
            'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://api.barraq.example',
        })

        with self.assertRaises(ValueError):
            resolve_list_setting(env, 'CSRF_TRUSTED_ORIGINS', fallback_keys=('DJANGO_CSRF_TRUSTED_ORIGINS',))

    def test_falls_back_to_default_when_nothing_is_set(self):
        env = _env_from({})

        result = resolve_list_setting(
            env, 'CSRF_TRUSTED_ORIGINS',
            fallback_keys=('DJANGO_CSRF_TRUSTED_ORIGINS',),
            default=['https://cors-origin.example'],
        )

        self.assertEqual(result, ['https://cors-origin.example'])


class ProductionEnvironmentValidationTests(SimpleTestCase):
    def test_secret_key_rejects_low_entropy_value(self):
        with self.assertRaises(ValueError):
            validate_secret_key('a' * 60)

    def test_origin_requires_https_when_requested(self):
        with self.assertRaises(ValueError):
            validate_origin_url(
                'http://dashboard.example',
                setting_name='CORS_ALLOWED_ORIGINS',
                require_https=True,
            )

    def test_origin_rejects_paths_and_credentials(self):
        with self.assertRaises(ValueError):
            validate_origin_url(
                'https://user:secret@dashboard.example/app',
                setting_name='CORS_ALLOWED_ORIGINS',
                require_https=True,
            )

    def test_allowed_hosts_cannot_use_wildcard(self):
        with self.assertRaises(ValueError):
            validate_allowed_hosts(['*'], public_api_hostname='api.example')

    def test_allowed_hosts_must_cover_public_api_host(self):
        with self.assertRaises(ValueError):
            validate_allowed_hosts(['dashboard.example'], public_api_hostname='api.example')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class SystemAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='system-user@example.com',
            password='StrongPass123',
            full_name='System User',
        )
        self.other_user = User.objects.create_user(
            email='other-system-user@example.com',
            password='StrongPass123',
            full_name='Other System User',
        )
        self.admin = User.objects.create_superuser(
            email='system-admin@example.com',
            password='StrongPass123',
            full_name='System Admin',
        )

    def authenticate(self, user=None):
        self.client.force_authenticate(user or self.user)

    def seed_data(self):
        call_command('seed_academic_data')

    def test_health_endpoint_returns_200(self):
        response = self.client.get(reverse('health-check'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['status'], 'ready')

    def test_meta_endpoint_returns_200(self):
        response = self.client.get(reverse('project-meta'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['api_version'], 'v1')
        self.assertEqual(response.data['data']['version'], '4.0.0')
        self.assertTrue(response.data['data']['features']['quizzes'])

    def test_seed_academic_data_runs_successfully(self):
        self.seed_data()

        self.assertEqual(EducationStage.objects.count(), 4)
        self.assertEqual(Subject.objects.count(), 22)

    def test_seed_academic_data_is_idempotent(self):
        self.seed_data()
        self.seed_data()

        self.assertEqual(EducationStage.objects.count(), 4)
        self.assertEqual(Subject.objects.count(), 22)

    def test_subject_filters_work_after_seed(self):
        self.seed_data()
        stage = EducationStage.objects.get(name='الابتدائية')

        response = self.client.get(
            reverse('subjects'),
            {'education_stage': stage.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 5)
        self.assertTrue(
            all(item['education_stage'] == stage.id for item in response.data['results'])
        )

    def test_unauthenticated_user_cannot_access_study_plans(self):
        response = self.client.get(reverse('study-plan-list'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(response.data['success'])

    def test_unauthenticated_user_cannot_access_quizzes(self):
        response = self.client.get(reverse('quiz-list'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(response.data['success'])

    def test_user_cannot_see_other_user_study_plan(self):
        self.seed_data()
        subject = Subject.objects.filter(name='الرياضيات').order_by('id').first()
        plan = StudyPlan.objects.create(
            user=self.other_user,
            title='Other User Plan',
            subject=subject,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=2),
            daily_study_minutes=60,
            difficulty_level=StudyPlan.DifficultyLevel.MEDIUM,
            generation_type=StudyPlan.GenerationType.MANUAL,
            status=StudyPlan.Status.ACTIVE,
        )

        self.authenticate(self.user)
        response = self.client.get(reverse('study-plan-detail', args=[plan.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_user_cannot_see_other_user_quiz(self):
        self.seed_data()
        subject = Subject.objects.filter(name='الفيزياء').order_by('id').first()
        quiz = Quiz.objects.create(
            user=self.other_user,
            subject=subject,
            title='Other User Quiz',
            topic='Forces',
            difficulty_level='medium',
            quiz_type='practice',
            generation_type='manual',
            status='published',
            questions_count=0,
        )

        self.authenticate(self.user)
        response = self.client.get(reverse('quiz-detail', args=[quiz.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_schema_endpoint_returns_200(self):
        self.authenticate(self.admin)
        response = self.client.get(reverse('api-schema'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_docs_endpoint_returns_200(self):
        self.authenticate(self.admin)
        response = self.client.get(reverse('api-docs'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
