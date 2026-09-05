from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Transcription

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class TranscriptionApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='audio-user@example.com', password='StrongPass123', full_name='Audio User',
        )
        self.other_user = User.objects.create_user(
            email='other-audio-user@example.com', password='StrongPass123', full_name='Other Audio User',
        )
        self.transcription = Transcription.objects.create(
            user=self.user,
            title='Physics Lecture',
            full_transcript='Raw transcript text.',
            cleaned_transcript='Cleaned transcript text.',
        )
        Transcription.objects.create(
            user=self.other_user, title='Someone else\'s lecture', full_transcript='...',
        )

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(reverse('transcription-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_only_sees_their_own_transcriptions(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse('transcription-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item['id'] for item in response.data['results']}
        self.assertEqual(ids, {self.transcription.id})

    def test_retrieve_returns_full_detail(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse('transcription-detail', args=[self.transcription.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['cleaned_transcript'], 'Cleaned transcript text.')

    def test_cannot_retrieve_another_users_transcription(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(reverse('transcription-detail', args=[self.transcription.id]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
