from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


def error_code(response):
    """DRF wraps a raised `serializers.ValidationError({'error_code': ...})`'s
    value in a list (standard field-error shape) -- unwrap it for assertions."""
    value = response.data.get('error_code')
    return str(value[0]) if isinstance(value, list) else value


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class UserSoftDeleteTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='soft-delete@example.com',
            password='StrongPass123',
            full_name='Soft Delete User',
        )

    def test_delete_marks_user_deleted_instead_of_removing_row(self):
        user_id = self.user.id

        self.user.delete()

        self.assertFalse(User.objects.filter(id=user_id).exists())
        self.assertTrue(User.all_objects.filter(id=user_id).exists())
        deleted_user = User.all_objects.get(id=user_id)
        self.assertTrue(deleted_user.is_deleted)
        self.assertIsNotNone(deleted_user.deleted_at)

    def test_soft_deleted_user_cannot_be_fetched_by_natural_key(self):
        self.user.delete()

        with self.assertRaises(User.DoesNotExist):
            User.objects.get_by_natural_key('soft-delete@example.com')

    def test_hard_delete_permanently_removes_row(self):
        user_id = self.user.id

        self.user.hard_delete()

        self.assertFalse(User.all_objects.filter(id=user_id).exists())


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class UserAuthTests(APITestCase):
    @patch('apps.users.serializers.send_email_otp.delay')
    def test_registration_normalizes_email_and_creates_student_profile(self, mocked_delay):
        response = self.client.post(
            reverse('register'),
            {
                'email': 'Student@Example.COM',
                'full_name': 'Student User',
                'password': 'A-Strong-Pass-123',
                'password_confirm': 'A-Strong-Pass-123',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email='student@example.com')
        self.assertTrue(hasattr(user, 'student_profile'))
        # A freshly registered account is unverified until it completes the
        # email-OTP flow -- this is exactly what email-OTP-at-registration
        # means (see EmailOTP / VerifyEmailOTPView).
        self.assertFalse(user.is_verified)
        mocked_delay.assert_called_once()

    @patch('apps.users.serializers.send_email_otp.delay')
    def test_email_uniqueness_is_case_insensitive(self, mocked_delay):
        User.objects.create_user(email='student@example.com', password='StrongPass123!', full_name='Student')
        response = self.client.post(
            reverse('register'),
            {
                'email': 'STUDENT@example.com',
                'full_name': 'Duplicate',
                'password': 'A-Strong-Pass-123',
                'password_confirm': 'A-Strong-Pass-123',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_returns_tokens_and_user(self):
        # Directly created via create_user() (not the register endpoint), so
        # `is_verified` keeps the model's default of True -- exercises the
        # "existing/pre-OTP account" path, unaffected by the new gate.
        user = User.objects.create_user(email='login@example.com', password='StrongPass123!', full_name='Login User')
        response = self.client.post(reverse('login'), {'email': user.email, 'password': 'StrongPass123!'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['id'], user.id)

    def test_login_blocked_until_email_verified(self):
        User.objects.create_user(
            email='unverified@example.com', password='StrongPass123!', full_name='Unverified User', is_verified=False,
        )
        response = self.client.post(
            reverse('login'), {'email': 'unverified@example.com', 'password': 'StrongPass123!'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(error_code(response), 'email_not_verified')

    def test_change_password_requires_current_password(self):
        user = User.objects.create_user(email='change@example.com', password='StrongPass123!', full_name='Change User')
        self.client.force_authenticate(user=user)
        response = self.client.post(
            reverse('change-password'),
            {'current_password': 'wrong', 'new_password': 'Another-Strong-Pass-123'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('apps.users.views.send_password_reset_email.delay')
    def test_password_reset_request_does_not_disclose_account_existence(self, mocked_delay):
        User.objects.create_user(email='reset@example.com', password='StrongPass123!', full_name='Reset User')
        existing = self.client.post(reverse('password-reset'), {'email': 'reset@example.com'}, format='json')
        missing = self.client.post(reverse('password-reset'), {'email': 'missing@example.com'}, format='json')
        self.assertEqual(existing.status_code, status.HTTP_200_OK)
        self.assertEqual(missing.status_code, status.HTTP_200_OK)
        self.assertEqual(existing.data['message'], missing.data['message'])
        mocked_delay.assert_called_once()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class EmailOTPTests(APITestCase):
    def setUp(self):
        from apps.users.services import issue_email_otp

        self.user = User.objects.create_user(
            email='otp-user@example.com', password='StrongPass123!', full_name='Otp User', is_verified=False,
        )
        self.code = issue_email_otp(self.user)

    def test_correct_code_verifies_and_logs_in(self):
        response = self.client.post(
            reverse('verify-email'), {'email': self.user.email, 'code': self.code}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['id'], self.user.id)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_verified)

        # Now that the account is verified, ordinary login succeeds too.
        login = self.client.post(
            reverse('login'), {'email': self.user.email, 'password': 'StrongPass123!'}, format='json',
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_wrong_code_is_rejected_and_does_not_verify(self):
        response = self.client.post(
            reverse('verify-email'), {'email': self.user.email, 'code': '000000'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(error_code(response), 'otp_invalid')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_verified)

    def test_expired_code_is_rejected(self):
        from datetime import timedelta

        from django.utils import timezone

        otp = self.user.email_otps.get(consumed_at__isnull=True)
        otp.expires_at = timezone.now() - timedelta(seconds=1)
        otp.save(update_fields=['expires_at'])

        response = self.client.post(
            reverse('verify-email'), {'email': self.user.email, 'code': self.code}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(error_code(response), 'otp_expired')

    def test_too_many_wrong_attempts_locks_the_code(self):
        for _ in range(5):
            self.client.post(reverse('verify-email'), {'email': self.user.email, 'code': '000000'}, format='json')

        response = self.client.post(
            reverse('verify-email'), {'email': self.user.email, 'code': self.code}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(error_code(response), 'otp_too_many_attempts')

    @patch('apps.users.views.send_email_otp.delay')
    def test_resend_is_rate_limited_by_cooldown(self, mocked_delay):
        # setUp already issued an OTP moments ago, so we're still inside the
        # 60s cooldown window.
        response = self.client.post(reverse('resend-otp'), {'email': self.user.email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(error_code(response), 'otp_resend_cooldown')
        self.assertIn('retry_after_seconds', response.data)
        mocked_delay.assert_not_called()

    @patch('apps.users.views.send_email_otp.delay')
    def test_resend_does_not_disclose_account_existence(self, mocked_delay):
        # Age the OTP from setUp past the cooldown window first, so the
        # "existing account" branch reaches the generic response instead of
        # the (also generic-looking, but distinct) cooldown error.
        from datetime import timedelta

        from django.utils import timezone

        otp = self.user.email_otps.get(consumed_at__isnull=True)
        otp.created_at = timezone.now() - timedelta(seconds=120)
        otp.save(update_fields=['created_at'])

        existing = self.client.post(reverse('resend-otp'), {'email': self.user.email}, format='json')
        missing = self.client.post(reverse('resend-otp'), {'email': 'nobody@example.com'}, format='json')
        self.assertEqual(existing.status_code, status.HTTP_200_OK)
        self.assertEqual(missing.status_code, status.HTTP_200_OK)
        self.assertEqual(existing.data['message'], missing.data['message'])
        mocked_delay.assert_called_once()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class AccountDeletionTests(APITestCase):
    """DELETE /api/v1/users/me/ -- authenticated self-service account deletion."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='delete-me@example.com', password='StrongPass123!', full_name='Delete Me',
        )
        login = self.client.post(
            reverse('login'), {'email': self.user.email, 'password': 'StrongPass123!'}, format='json',
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.access = login.data['access']
        self.refresh = login.data['refresh']

    def _authenticate(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access}')

    def test_unauthenticated_delete_is_rejected(self):
        response = self.client.delete(reverse('user-me'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_deleted)

    def test_authenticated_delete_succeeds_and_anonymizes(self):
        self._authenticate()
        response = self.client.delete(reverse('user-me'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('message', response.data)

        deleted = User.all_objects.get(pk=self.user.pk)
        self.assertTrue(deleted.is_deleted)
        self.assertFalse(deleted.is_active)
        self.assertIsNotNone(deleted.deleted_at)
        # PII is anonymized, not merely flagged.
        self.assertNotEqual(deleted.email, 'delete-me@example.com')
        self.assertEqual(deleted.full_name, '')
        self.assertEqual(deleted.phone_number, '')
        # Excluded from the default (soft-delete-aware) manager afterward.
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

    def test_cannot_authenticate_after_deletion(self):
        self._authenticate()
        self.client.delete(reverse('user-me'))

        # The very access token used to delete the account, still unexpired,
        # is rejected on the next request -- not just future logins.
        me = self.client.get(reverse('user-me'))
        self.assertEqual(me.status_code, status.HTTP_401_UNAUTHORIZED)

        login = self.client.post(
            reverse('login'), {'email': 'delete-me@example.com', 'password': 'StrongPass123!'}, format='json',
        )
        self.assertEqual(login.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_token_is_blacklisted_after_deletion(self):
        self._authenticate()
        self.client.delete(reverse('user-me'))
        self.client.credentials()  # the refresh endpoint itself needs no access token

        refresh_response = self.client.post(reverse('token-refresh'), {'refresh': self.refresh}, format='json')
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_repeated_deletion_is_idempotent(self):
        from apps.users.services import delete_user_account

        self._authenticate()
        first = self.client.delete(reverse('user-me'))
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        # The normal flow can never reach the view a second time as this user
        # (no valid token can be produced once deleted) -- call the service
        # function directly to prove it tolerates being re-applied safely.
        delete_user_account(self.user)

        deleted = User.all_objects.get(pk=self.user.pk)
        self.assertTrue(deleted.is_deleted)
        self.assertEqual(deleted.email, f'deleted-user-{self.user.pk}@deleted.baraq.invalid')

    def test_related_data_is_preserved_not_cascaded(self):
        from apps.students.models import StudentProfile

        profile, _ = StudentProfile.objects.get_or_create(user=self.user)

        self._authenticate()
        response = self.client.delete(reverse('user-me'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Related rows survive a soft-delete -- only the account itself
        # becomes unreachable (kept for legal/audit purposes, not cascaded).
        self.assertTrue(StudentProfile.objects.filter(pk=profile.pk).exists())


class CeleryQueueRoutingTests(TestCase):
    """Latency-sensitive user email must route to its own `critical` queue --
    never share `default` with AI-dispatch/source-processing work (see
    CELERY_TASK_ROUTES in config/settings.py)."""

    def test_otp_and_password_reset_email_route_to_the_critical_queue(self):
        from config.celery import app

        self.assertEqual(app.amqp.router.route({}, 'users.send_email_otp')['queue'].name, 'critical')
        self.assertEqual(
            app.amqp.router.route({}, 'users.send_password_reset_email')['queue'].name, 'critical',
        )

    def test_unrelated_tasks_fall_back_to_the_default_queue(self):
        from config.celery import app

        self.assertEqual(app.amqp.router.route({}, 'ai_integration.dispatch_job')['queue'].name, 'default')
        self.assertEqual(app.amqp.router.route({}, 'sources.process_source')['queue'].name, 'default')
