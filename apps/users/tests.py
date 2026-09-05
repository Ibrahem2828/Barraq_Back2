from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


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
    def test_registration_normalizes_email_and_creates_student_profile(self):
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

    def test_email_uniqueness_is_case_insensitive(self):
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
        user = User.objects.create_user(email='login@example.com', password='StrongPass123!', full_name='Login User')
        response = self.client.post(reverse('login'), {'email': user.email, 'password': 'StrongPass123!'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['id'], user.id)

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
