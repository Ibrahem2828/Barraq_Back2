from __future__ import annotations

import threading
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.db import IntegrityError, close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.students.models import StudentProfile
from apps.users.models import PendingRegistration
from apps.users.services import cleanup_expired_pending_registrations

User = get_user_model()


def domain_code(response) -> str:
    return str(response.data.get('code'))


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class PendingRegistrationFlowTests(APITestCase):
    password = 'A-Strong-Pass-123'

    def setUp(self):
        cache.clear()

    def payload(self, **overrides):
        data = {
            'email': 'Learner@Example.COM',
            'full_name': 'Learner One',
            'phone_number': '+963 111 222 333',
            'password': self.password,
            'password_confirm': self.password,
        }
        data.update(overrides)
        return data

    @patch('apps.users.views.send_email_otp.delay')
    def register(self, mocked_delay, **overrides):
        response = self.client.post(reverse('register'), self.payload(**overrides), format='json')
        code = mocked_delay.call_args.args[1] if mocked_delay.call_count else None
        return response, code, mocked_delay

    def test_register_creates_only_hashed_pending_registration(self):
        response, code, mocked_delay = self.register()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['verification_required'])
        self.assertEqual(response.data['email'], 'learner@example.com')
        self.assertFalse(User.objects.filter(email='learner@example.com').exists())
        self.assertFalse(StudentProfile.objects.exists())
        pending = PendingRegistration.objects.get(normalized_email='learner@example.com')
        self.assertNotEqual(pending.password_hash, self.password)
        self.assertNotIn(self.password, pending.password_hash)
        self.assertTrue(check_password(self.password, pending.password_hash))
        self.assertNotEqual(pending.otp_hash, code)
        self.assertNotIn(code, pending.otp_hash)
        self.assertTrue(check_password(code, pending.otp_hash))
        mocked_delay.assert_called_once_with('learner@example.com', code)

    def test_unverified_registration_cannot_authenticate_before_verification(self):
        self.register()

        login = self.client.post(
            reverse('login'),
            {'email': 'learner@example.com', 'password': self.password},
            format='json',
        )
        self.assertNotEqual(login.status_code, status.HTTP_200_OK)
        self.assertFalse(User.objects.filter(email='learner@example.com').exists())

    def test_invalid_or_expired_otp_never_creates_a_user(self):
        _, code, _ = self.register()
        wrong_code = '999999' if code != '999999' else '888888'
        wrong = self.client.post(reverse('verify-email'), {'email': 'learner@example.com', 'code': wrong_code}, format='json')
        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(domain_code(wrong), 'otp_invalid')
        self.assertFalse(User.objects.filter(email='learner@example.com').exists())

        pending = PendingRegistration.objects.get(normalized_email='learner@example.com')
        pending.otp_expires_at = timezone.now() - timedelta(seconds=1)
        pending.save(update_fields=['otp_expires_at'])
        expired = self.client.post(reverse('verify-email'), {'email': 'learner@example.com', 'code': code}, format='json')
        self.assertEqual(expired.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(domain_code(expired), 'otp_expired')
        self.assertFalse(User.objects.filter(email='learner@example.com').exists())

    def test_valid_otp_creates_user_and_profile_atomically_with_original_password(self):
        _, code, _ = self.register()

        response = self.client.post(reverse('verify-email'), {'email': 'learner@example.com', 'code': code}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        user = User.objects.get(email='learner@example.com')
        self.assertTrue(user.is_verified)
        self.assertTrue(user.check_password(self.password))
        self.assertTrue(StudentProfile.objects.filter(user=user).exists())
        self.assertFalse(PendingRegistration.objects.filter(normalized_email=user.email).exists())

        login = self.client.post(
            reverse('login'),
            {'email': 'LEARNER@EXAMPLE.COM', 'password': self.password},
            format='json',
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_otp_replay_and_verified_email_never_issue_tokens(self):
        _, code, _ = self.register()
        first = self.client.post(reverse('verify-email'), {'email': 'learner@example.com', 'code': code}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        replay = self.client.post(reverse('verify-email'), {'email': 'learner@example.com', 'code': code}, format='json')
        self.assertEqual(replay.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn('access', replay.data)
        self.assertEqual(User.objects.filter(email='learner@example.com').count(), 1)

        verified = User.objects.create_user(
            email='already-verified@example.com', password=self.password, full_name='Verified', is_verified=True,
        )
        bypass = self.client.post(
            reverse('verify-email'), {'email': verified.email, 'code': '123456'}, format='json',
        )
        self.assertEqual(bypass.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn('access', bypass.data)

    def test_resend_replaces_the_old_code_and_enforces_cooldown(self):
        _, first_code, _ = self.register()
        cooldown = self.client.post(reverse('resend-otp'), {'email': 'learner@example.com'}, format='json')
        self.assertEqual(cooldown.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(domain_code(cooldown), 'otp_resend_cooldown')

        pending = PendingRegistration.objects.get(normalized_email='learner@example.com')
        pending.last_otp_sent_at = timezone.now() - timedelta(seconds=61)
        pending.save(update_fields=['last_otp_sent_at'])
        with patch('apps.users.views.send_email_otp.delay') as mocked_delay:
            resent = self.client.post(reverse('resend-otp'), {'email': 'learner@example.com'}, format='json')
        second_code = mocked_delay.call_args.args[1]
        self.assertEqual(resent.status_code, status.HTTP_200_OK)
        self.assertNotEqual(first_code, second_code)

        old = self.client.post(reverse('verify-email'), {'email': 'learner@example.com', 'code': first_code}, format='json')
        self.assertEqual(old.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(domain_code(old), 'otp_invalid')
        new = self.client.post(reverse('verify-email'), {'email': 'learner@example.com', 'code': second_code}, format='json')
        self.assertEqual(new.status_code, status.HTTP_200_OK)

    @override_settings(OTP_MAX_ATTEMPTS=2)
    def test_attempt_limit_requires_a_new_code(self):
        _, code, _ = self.register()
        wrong_code = '999999' if code != '999999' else '888888'
        for _ in range(2):
            self.client.post(reverse('verify-email'), {'email': 'learner@example.com', 'code': wrong_code}, format='json')

        pending = PendingRegistration.objects.get(normalized_email='learner@example.com')
        self.assertEqual(pending.otp_attempt_count, 2)

        response = self.client.post(reverse('verify-email'), {'email': 'learner@example.com', 'code': code}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(domain_code(response), 'otp_too_many_attempts')
        self.assertFalse(User.objects.filter(email='learner@example.com').exists())

    @override_settings(OTP_MAX_SENDS_PER_WINDOW=2)
    def test_per_email_send_limit_is_enforced_server_side(self):
        self.register()
        pending = PendingRegistration.objects.get(normalized_email='learner@example.com')
        pending.last_otp_sent_at = timezone.now() - timedelta(seconds=61)
        pending.save(update_fields=['last_otp_sent_at'])
        with patch('apps.users.views.send_email_otp.delay'):
            self.client.post(reverse('resend-otp'), {'email': 'learner@example.com'}, format='json')

        pending.refresh_from_db()
        pending.last_otp_sent_at = timezone.now() - timedelta(seconds=61)
        pending.save(update_fields=['last_otp_sent_at'])
        blocked = self.client.post(reverse('resend-otp'), {'email': 'learner@example.com'}, format='json')
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(domain_code(blocked), 'otp_resend_limit_exceeded')

    def test_existing_user_is_rejected_without_creating_a_pending_duplicate(self):
        User.objects.create_user(email='learner@example.com', password=self.password, full_name='Existing')

        response, _code, mocked_delay = self.register()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(domain_code(response), 'email_already_registered')
        self.assertFalse(PendingRegistration.objects.filter(normalized_email='learner@example.com').exists())
        mocked_delay.assert_not_called()

    def test_expired_pending_registration_can_restart_with_new_validated_data(self):
        self.register()
        pending = PendingRegistration.objects.get(normalized_email='learner@example.com')
        pending.otp_expires_at = timezone.now() - timedelta(seconds=1)
        pending.save(update_fields=['otp_expires_at'])
        new_password = 'Another-Strong-Pass-123'

        with patch('apps.users.views.send_email_otp.delay') as mocked_delay:
            response = self.client.post(
                reverse('register'),
                self.payload(full_name='Renewed Learner', password=new_password, password_confirm=new_password),
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        pending.refresh_from_db()
        self.assertEqual(pending.full_name, 'Renewed Learner')
        self.assertTrue(check_password(new_password, pending.password_hash))
        self.assertTrue(check_password(mocked_delay.call_args.args[1], pending.otp_hash))

    def test_broker_enqueue_failure_keeps_pending_data_but_never_creates_user(self):
        with patch('apps.users.views.send_email_otp.delay', side_effect=OSError('broker unavailable')):
            response = self.client.post(reverse('register'), self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(domain_code(response), 'email_delivery_unavailable')
        self.assertTrue(PendingRegistration.objects.filter(normalized_email='learner@example.com').exists())
        self.assertFalse(User.objects.filter(email='learner@example.com').exists())

    def test_verification_failure_rolls_back_user_and_keeps_pending_registration(self):
        _, code, _ = self.register()
        with patch('apps.students.models.StudentProfile.objects.get_or_create', side_effect=IntegrityError('profile conflict')):
            response = self.client.post(reverse('verify-email'), {'email': 'learner@example.com', 'code': code}, format='json')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(domain_code(response), 'registration_conflict')
        self.assertFalse(User.objects.filter(email='learner@example.com').exists())
        self.assertTrue(PendingRegistration.objects.filter(normalized_email='learner@example.com').exists())

    def test_cleanup_removes_only_stale_pending_registrations(self):
        self.register()
        old = PendingRegistration.objects.get(normalized_email='learner@example.com')
        PendingRegistration.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(hours=25))
        User.objects.create_user(email='permanent@example.com', password=self.password, full_name='Permanent')

        deleted = cleanup_expired_pending_registrations()

        self.assertEqual(deleted, 1)
        self.assertFalse(PendingRegistration.objects.filter(pk=old.pk).exists())
        self.assertTrue(User.objects.filter(email='permanent@example.com').exists())


@skipUnless(connection.vendor == 'postgresql', 'requires PostgreSQL row locking semantics')
class PendingRegistrationConcurrencyTests(TransactionTestCase):
    """A real two-request race test for the select_for_update account boundary."""

    reset_sequences = True

    def setUp(self):
        now = timezone.now()
        self.email = 'race@example.com'
        self.code = '918273'
        PendingRegistration.objects.create(
            normalized_email=self.email,
            full_name='Race Learner',
            password_hash=make_password('A-Strong-Pass-123'),
            otp_hash=make_password(self.code),
            otp_expires_at=now + timedelta(minutes=10),
            otp_send_count=1,
            otp_send_window_started_at=now,
            last_otp_sent_at=now,
        )

    def test_two_simultaneous_verifications_create_exactly_one_user(self):
        barrier = threading.Barrier(2)
        results: list[tuple[int, bool]] = []
        result_lock = threading.Lock()

        def verify():
            close_old_connections()
            client = APIClient()
            try:
                barrier.wait(timeout=10)
                response = client.post(reverse('verify-email'), {'email': self.email, 'code': self.code}, format='json')
                with result_lock:
                    results.append((response.status_code, 'access' in response.data))
            finally:
                close_old_connections()

        threads = [threading.Thread(target=verify), threading.Thread(target=verify)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(status_code == status.HTTP_200_OK for status_code, _ in results), 1)
        self.assertEqual(sum(has_access for _, has_access in results), 1)
        self.assertEqual(User.objects.filter(email=self.email).count(), 1)
        user = User.objects.get(email=self.email)
        self.assertEqual(StudentProfile.objects.filter(user=user).count(), 1)
        self.assertFalse(PendingRegistration.objects.filter(normalized_email=self.email).exists())
