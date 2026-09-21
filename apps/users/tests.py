from unittest import mock
from unittest.mock import patch

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle

User = get_user_model()


def error_code(response):
    """Return a stable domain code while keeping legacy assertions readable."""

    domain_code = response.data.get('code')
    if domain_code and domain_code != 'validation_error':
        return str(domain_code)
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
    # DRF's ScopedRateThrottle for 'login' persists in the cache for the
    # whole test process (unlike the database, the cache is not reset per
    # test). This file alone makes 9+ login calls across its classes; left
    # uncleared, the shared counter eventually returns 429 to a later,
    # unrelated test and fails it for a reason that has nothing to do with
    # what that test checks.
    def setUp(self):
        cache.clear()

    @patch('apps.users.views.send_email_otp.delay')
    def test_registration_normalizes_email_and_creates_pending_registration(self, mocked_delay):
        from apps.users.models import PendingRegistration

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
        self.assertFalse(User.objects.filter(email='student@example.com').exists())
        pending = PendingRegistration.objects.get(normalized_email='student@example.com')
        self.assertTrue(pending.password_hash)
        self.assertTrue(pending.otp_hash)
        self.assertTrue(response.data['verification_required'])
        mocked_delay.assert_called_once()

    @patch('apps.users.views.send_email_otp.delay')
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
        self.assertEqual(error_code(response), 'email_already_registered')

    @patch('apps.users.views.RegisterView.throttle_classes', [ScopedRateThrottle])
    @patch.object(ScopedRateThrottle, 'THROTTLE_RATES', {'register': '1/minute'})
    @patch('apps.users.views.send_email_otp.delay')
    def test_register_applies_server_side_scoped_rate_limit(self, mocked_delay):
        first = self.client.post(
            reverse('register'),
            {
                'email': 'first@example.com',
                'full_name': 'First Student',
                'password': 'StrongPass123!',
                'password_confirm': 'StrongPass123!',
            },
            format='json',
        )
        second = self.client.post(
            reverse('register'),
            {
                'email': 'second@example.com',
                'full_name': 'Second Student',
                'password': 'StrongPass123!',
                'password_confirm': 'StrongPass123!',
            },
            format='json',
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(error_code(second), 'rate_limited')
        self.assertEqual(mocked_delay.call_count, 1)

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
class LogoutRevocationTests(APITestCase):
    """Historical regression: logout must revoke the refresh token server-side.

    Baraq previously logged a user out by discarding cookies client-side only;
    the backend refresh token stayed live and could mint new access tokens
    indefinitely after a "logged out" browser closed. The fix routes the
    refresh token through LogoutView, which blacklists it. Nothing before this
    test proved that call actually revokes anything -- the web-side test only
    proves the client sends the right request, not that the backend honours it.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="logout-regression@example.com",
            password="A-Strong-Pass-123",
            full_name="Logout Regression",
            is_verified=True,
        )
        login = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": "A-Strong-Pass-123"},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.access = login.data["access"]
        self.refresh = login.data["refresh"]

    def test_logout_blacklists_the_refresh_token(self):
        response = self.client.post(
            reverse("logout"),
            {"refresh": self.refresh},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.access}",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_a_blacklisted_refresh_token_cannot_mint_a_new_access_token(self):
        """The actual security property: revocation, not just a 204."""
        logout_response = self.client.post(
            reverse("logout"),
            {"refresh": self.refresh},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.access}",
        )
        self.assertEqual(logout_response.status_code, status.HTTP_204_NO_CONTENT)

        reuse_response = self.client.post(
            reverse("token-refresh"), {"refresh": self.refresh}, format="json"
        )
        self.assertEqual(
            reuse_response.status_code,
            status.HTTP_401_UNAUTHORIZED,
            "a refresh token survived logout and could still mint a new access token",
        )

    def test_logout_requires_authentication(self):
        """An unauthenticated caller cannot blacklist an arbitrary refresh token."""
        response = self.client.post(reverse("logout"), {"refresh": self.refresh}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_an_invalid_refresh_token_is_rejected_not_silently_accepted(self):
        response = self.client.post(
            reverse("logout"),
            {"refresh": "not-a-real-token"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.access}",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class EmailOTPTests(APITestCase):
    def setUp(self):
        cache.clear()
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
        wrong_code = '999999' if self.code != '999999' else '888888'
        response = self.client.post(
            reverse('verify-email'), {'email': self.user.email, 'code': wrong_code}, format='json',
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
        wrong_code = '999999' if self.code != '999999' else '888888'
        for _ in range(5):
            self.client.post(reverse('verify-email'), {'email': self.user.email, 'code': wrong_code}, format='json')

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
        cache.clear()
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


class OtpDeliveryHandoffTests(TestCase):
    """The SMTP handoff boundary.

    Real mailbox delivery cannot be proven from here -- it needs production
    SMTP credentials and outbound network -- so everything up to and
    including the handoff is proven, and delivery itself is an RC gate.
    """

    def test_a_refused_message_is_a_failure_not_a_silent_success(self):
        """send_mail returning 0 means the backend accepted nothing.

        That used to be returned verbatim, so Celery marked the task done and
        a student waited for a code that was never sent.
        """
        from apps.users.tasks import send_email_otp

        with (
            mock.patch('apps.users.tasks.send_mail', return_value=0),
            mock.patch.object(send_email_otp, 'retry', side_effect=Retry()) as retry,
            self.assertRaises(Retry),
        ):
            send_email_otp('student@example.com', '123456')

        self.assertEqual(retry.call_count, 1)

    def test_an_accepted_message_reports_success(self):
        from apps.users.tasks import send_email_otp

        with mock.patch('apps.users.tasks.send_mail', return_value=1) as send:
            result = send_email_otp('student@example.com', '123456')

        self.assertEqual(result, 1)
        self.assertEqual(send.call_count, 1)

    def test_the_otp_code_never_reaches_the_logs(self):
        """A code in a log file is a code an operator can use."""
        from apps.users.tasks import send_email_otp

        with (
            mock.patch('apps.users.tasks.send_mail', return_value=1),
            self.assertLogs('apps.users.tasks', level='DEBUG') as captured,
        ):
            send_email_otp('student@example.com', '987654')

        joined = '\n'.join(captured.output)
        self.assertNotIn('987654', joined)
        self.assertNotIn('student@example.com', joined)

    def test_the_message_body_carries_the_code_to_the_recipient(self):
        """The counterpart: the code must actually be in what we hand SMTP,
        so a passing 'no code in logs' test cannot mean 'no code anywhere'."""
        from apps.users.tasks import send_email_otp

        with mock.patch('apps.users.tasks.send_mail', return_value=1) as send:
            send_email_otp('student@example.com', '987654')

        _subject, body, _from_email, recipients = send.call_args.args
        self.assertIn('987654', body)
        self.assertEqual(recipients, ['student@example.com'])
