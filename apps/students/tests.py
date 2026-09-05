from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.subjects.models import EducationStage

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class StudentProfileTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='profile@example.com', password='StrongPass123!', full_name='Profile User')
        self.stage = EducationStage.objects.create(name='University', order=1)
        self.client.force_authenticate(user=self.user)

    def test_setup_and_read_own_profile(self):
        response = self.client.post(
            reverse('student-setup-profile'),
            {
                'education_stage': self.stage.id,
                'grade_level': 'Year 2',
                'specialization': 'Medicine',
                'study_goal': 'Pass exams',
                'daily_study_hours': 3,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['is_setup_completed'])
        detail = self.client.get(reverse('student-profile'))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data['education_stage'], self.stage.id)
