from datetime import timedelta

import environ
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings
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

# The project test runner discovers each installed app through its historical
# ``tests.py`` module. Import the generated-contract surface sweep here so it
# is part of every unlabeled CI/test run as well as direct targeted runs.
from apps.common.test_api_surface import OpenAPISurfaceTests  # noqa: F401
from apps.common.test_deployment_config import (  # noqa: F401
    ProductionComposeTests,
    UploadSizeChainTests,
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


class SecureProxyForwardedProtoTests(SimpleTestCase):
    """Regression guard for the internal-service SSL-redirect bug: the web
    BFF and admin dashboard call Django directly over the private Docker
    network (bypassing the Caddy gateway, which normally sets this header
    for public traffic), so with SECURE_SSL_REDIRECT=True (the production
    default), Django's SecurityMiddleware 301-redirects any request that
    doesn't carry X-Forwarded-Proto: https to an HTTPS URL nothing
    internally serves. web/src/lib/api/backend.ts and
    Baraq_Dashboard_Professional/lib/api/{backend-http,auth/edge-session}.ts
    now set this header on every internal call; this test pins the Django
    side of that contract (SECURE_PROXY_SSL_HEADER, config/settings.py) so a
    future change can't silently break it again. Uses SecurityMiddleware
    directly (matching Django's own test suite for this middleware) rather
    than the full test client, since SECURE_SSL_REDIRECT is cached on the
    middleware instance at construction time and a mid-test
    override_settings() isn't guaranteed to reach an already-built chain.
    """

    def _run(self, **extra):
        middleware = SecurityMiddleware(lambda request: HttpResponse())
        # Deliberately NOT a health path: those are in SECURE_REDIRECT_EXEMPT
        # so container probes reach the app over loopback HTTP. This test is
        # about the X-Forwarded-Proto contract for ordinary internal calls,
        # and must use a path the redirect actually applies to.
        request = RequestFactory().get('/api/v1/student-sources/', **extra)
        return middleware(request)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_plain_http_without_forwarded_proto_header_is_redirected(self):
        response = self._run()

        self.assertEqual(response.status_code, 301)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_plain_http_with_forwarded_proto_https_header_is_not_redirected(self):
        response = self._run(HTTP_X_FORWARDED_PROTO='https')

        self.assertEqual(response.status_code, 200)


class ErrorEnvelopeContractTests(APITestCase):
    """The single machine-readable error contract every client branches on.

    Every error response carries a stable top-level ``code``. A domain
    exception that declares its own code (``file_size_limit_exceeded``,
    ``character_not_allowed``, ...) surfaces that code; everything else falls
    back to a status-derived one. Before this, the top-level code was
    *always* status-derived, so the real code stayed buried under ``errors``
    and clients reading the documented field saw ``permission_denied`` for
    every predictable plan failure.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='envelope@example.com',
            password='StrongPass123',
            full_name='Envelope User',
        )

    def assertEnvelope(self, response, *, status_code, code):
        self.assertEqual(response.status_code, status_code)
        for key in ('success', 'message', 'errors', 'code'):
            self.assertIn(key, response.data, f'envelope is missing "{key}"')
        self.assertFalse(response.data['success'])
        self.assertTrue(response.data['message'])
        self.assertEqual(response.data['code'], code)

    def test_authentication_error_has_a_stable_code(self):
        response = self.client.get(reverse('student-source-list'))
        self.assertEnvelope(response, status_code=401, code='authentication_error')

    def test_validation_error_keeps_field_errors_and_its_code(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(reverse('student-source-list'), {}, format='multipart')
        self.assertEnvelope(response, status_code=400, code='validation_error')
        self.assertTrue(response.data['errors'])

    def test_not_found_has_a_stable_code(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse('student-source-detail', args=[999999]))
        self.assertEnvelope(response, status_code=404, code='not_found')

    def test_subscription_file_limit_surfaces_its_domain_code(self):
        """The regression this contract exists for.

        A predictable, actionable failure must not reach the client as a
        generic `permission_denied` -- that is what made every client-side
        subscription-limit mapping dead code.
        """
        from apps.common.exceptions import custom_exception_handler
        from apps.subscriptions.exceptions import SubscriptionLimitExceeded

        exc = SubscriptionLimitExceeded(
            'حجم الملف أكبر من الحد المسموح (50MB).',
            code='file_size_limit_exceeded',
            limit=50,
            usage=60,
        )
        response = custom_exception_handler(exc, {'request': None})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'file_size_limit_exceeded')
        self.assertNotEqual(response.data['code'], 'permission_denied')
        # The nested copy stays, so any client already reading it keeps working.
        self.assertEqual(response.data['errors']['code'], 'file_size_limit_exceeded')
        self.assertEqual(int(response.data['errors']['limit']), 50)

    def test_subscription_feature_block_surfaces_its_domain_code(self):
        from apps.common.exceptions import custom_exception_handler
        from apps.subscriptions.exceptions import SubscriptionFeatureNotAllowed

        exc = SubscriptionFeatureNotAllowed(
            'هذه الشخصية غير متاحة في خطتك الحالية.',
            code='character_not_allowed',
            character='kholasa',
        )
        response = custom_exception_handler(exc, {'request': None})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'character_not_allowed')

    def test_plain_permission_denied_keeps_the_generic_code(self):
        """Backward compatibility: only an explicitly declared domain code is
        promoted. A bare PermissionDenied must not change shape."""
        from rest_framework.exceptions import PermissionDenied

        from apps.common.exceptions import custom_exception_handler

        response = custom_exception_handler(PermissionDenied(), {'request': None})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'permission_denied')

    def test_rate_limit_has_its_own_code_not_request_error(self):
        from rest_framework.exceptions import Throttled

        from apps.common.exceptions import custom_exception_handler

        response = custom_exception_handler(Throttled(wait=30), {'request': None})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data['code'], 'rate_limited')

    def test_a_serializer_field_named_code_is_not_mistaken_for_a_domain_code(self):
        """Guard against promoting user input.

        SubscriptionPlan has a `code` field, so a validation error can legally
        contain an errors key called `code`. DRF renders field errors as
        lists, and only a bare string is promoted -- but assert it, because
        promoting attacker-influenced input into the contract would be worse
        than the bug this fixes.
        """
        from rest_framework.exceptions import ValidationError

        from apps.common.exceptions import custom_exception_handler

        response = custom_exception_handler(
            ValidationError({'code': 'this-is-a-field-error'}), {'request': None}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'validation_error')


class HealthProbeTests(APITestCase):
    """A health check must actually reach the application.

    The container probes hit plain HTTP on loopback, bypassing the gateway
    that sets X-Forwarded-Proto. With SECURE_SSL_REDIRECT on, Django answered
    301 -- and `curl --fail` does not treat a 301 as failure, so the probe
    reported healthy while never touching the app.
    """

    @override_settings(SECURE_SSL_REDIRECT=True, DEBUG=False)
    def test_liveness_answers_directly_over_plain_http(self):
        response = self.client.get(reverse('health-live'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # EnvelopeJSONRenderer wraps the payload under `data`.
        self.assertEqual(response.data['data']['status'], 'ok')

    @override_settings(SECURE_SSL_REDIRECT=True, DEBUG=False)
    def test_readiness_answers_directly_over_plain_http(self):
        response = self.client.get(reverse('health-ready'))

        self.assertIn(
            response.status_code,
            (status.HTTP_200_OK, status.HTTP_503_SERVICE_UNAVAILABLE),
        )
        self.assertNotEqual(response.status_code, status.HTTP_301_MOVED_PERMANENTLY)

    def test_the_exemption_is_limited_to_the_probe_paths(self):
        import re

        from django.conf import settings as django_settings

        patterns = [re.compile(p) for p in django_settings.SECURE_REDIRECT_EXEMPT]

        def exempt(path):
            return any(p.search(path.lstrip('/')) for p in patterns)

        for path in ('api/health/live/', 'api/v1/health/ready/', 'api/v1/health/'):
            self.assertTrue(exempt(path), path)
        for path in ('api/v1/student-sources/', 'api/v1/auth/login/', 'api/v1/admin/me/'):
            self.assertFalse(exempt(path), f'{path} must still be redirected to HTTPS')
