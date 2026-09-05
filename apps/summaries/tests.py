from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Summary

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class SummaryApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='summary-user@example.com', password='StrongPass123', full_name='Summary User',
        )
        self.other_user = User.objects.create_user(
            email='other-summary-user@example.com', password='StrongPass123', full_name='Other Summary User',
        )
        self.summary = Summary.objects.create(
            user=self.user,
            title='Chapter 3 summary',
            short_summary='Short version.',
            detailed_summary='Detailed version.',
            key_points=['point one', 'point two'],
            important_terms=['mitosis', 'meiosis'],
        )
        Summary.objects.create(user=self.other_user, title='Someone else\'s summary')

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(reverse('summary-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_only_sees_their_own_summaries(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse('summary-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item['id'] for item in response.data['results']}
        self.assertEqual(ids, {self.summary.id})

    def test_important_terms_is_a_flat_list_of_strings(self):
        # Regression guard: important_terms must stay list[str] end to end --
        # the AI service previously sent a {term: definition} dict here,
        # which this JSONField would have accepted silently either way.
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse('summary-detail', args=[self.summary.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['important_terms'], ['mitosis', 'meiosis'])

    def test_cannot_retrieve_another_users_summary(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(reverse('summary-detail', args=[self.summary.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
