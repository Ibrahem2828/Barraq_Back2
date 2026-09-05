from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Notification

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class NotificationApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='notif-user@example.com', password='StrongPass123', full_name='Notif User',
        )
        self.other_user = User.objects.create_user(
            email='other-notif-user@example.com', password='StrongPass123', full_name='Other Notif User',
        )
        self.notification = Notification.objects.create(
            user=self.user,
            category=Notification.Category.AI,
            title='Your quiz is ready',
            body='Fahes finished generating your quiz.',
        )
        Notification.objects.create(
            user=self.other_user, category=Notification.Category.SYSTEM, title='...', body='...',
        )

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(reverse('notification-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_only_sees_their_own_notifications(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse('notification-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item['id'] for item in response.data['results']}
        self.assertEqual(ids, {self.notification.id})

    def test_unread_count_reflects_only_unread_notifications(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse('notification-unread-count'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)

    def test_mark_read_then_unread_count_drops_to_zero(self):
        self.client.force_authenticate(user=self.user)
        mark_response = self.client.post(reverse('notification-mark-read', args=[self.notification.id]))
        self.assertEqual(mark_response.status_code, status.HTTP_200_OK)

        count_response = self.client.get(reverse('notification-unread-count'))
        self.assertEqual(count_response.data['count'], 0)

    def test_mark_all_read_only_affects_the_current_user(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(reverse('notification-mark-all-read'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['updated'], 1)
        other_notification = Notification.objects.get(user=self.other_user)
        self.assertIsNone(other_notification.read_at)
