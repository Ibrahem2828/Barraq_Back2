from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import StudentRecommendation

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class StudentRecommendationApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='analytics-user@example.com', password='StrongPass123', full_name='Analytics User',
        )
        self.other_user = User.objects.create_user(
            email='other-analytics-user@example.com', password='StrongPass123', full_name='Other Analytics User',
        )
        self.recommendation = StudentRecommendation.objects.create(
            user=self.user,
            title='Focus on algebra',
            summary='Weak topics detected in recent quizzes.',
            is_read=False,
        )
        StudentRecommendation.objects.create(user=self.other_user, title='Someone else\'s recommendation')

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(reverse('recommendation-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_only_sees_their_own_recommendations(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse('recommendation-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item['id'] for item in response.data['results']}
        self.assertEqual(ids, {self.recommendation.id})

    def test_mark_read_flips_the_flag(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(reverse('recommendation-mark-read', args=[self.recommendation.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.recommendation.refresh_from_db()
        self.assertTrue(self.recommendation.is_read)

    def test_cannot_mark_another_users_recommendation_read(self):
        other_recommendation = StudentRecommendation.objects.get(user=self.other_user)
        self.client.force_authenticate(user=self.user)
        response = self.client.post(reverse('recommendation-mark-read', args=[other_recommendation.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
