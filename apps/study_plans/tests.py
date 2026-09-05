from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.subjects.models import EducationStage, Subject

from .models import StudyPlan, StudyTask

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class StudyPlanAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='student1@example.com',
            password='StrongPass123',
            full_name='Student One',
        )
        self.other_user = User.objects.create_user(
            email='student2@example.com',
            password='StrongPass123',
            full_name='Student Two',
        )
        self.stage = EducationStage.objects.create(
            name='Secondary',
            description='Secondary stage',
            order=1,
        )
        self.subject = Subject.objects.create(
            name='Mathematics',
            education_stage=self.stage,
            grade_level='Grade 12',
            description='Core subject',
        )
        self.other_subject = Subject.objects.create(
            name='Physics',
            education_stage=self.stage,
            grade_level='Grade 12',
            description='Science subject',
        )

    def authenticate(self, user=None):
        self.client.force_authenticate(user or self.user)

    def create_plan_via_api(self, **overrides):
        self.authenticate()
        today = timezone.localdate()
        payload = {
            'title': 'Math Review Plan',
            'description': 'Plan before the final test',
            'subject': self.subject.id,
            'start_date': str(today),
            'end_date': str(today + timedelta(days=2)),
            'daily_study_minutes': 120,
            'goal': 'Review chapters and solve exercises',
            'difficulty_level': 'medium',
            'generation_type': 'manual',
        }
        payload.update(overrides)
        return self.client.post(reverse('study-plan-list'), payload, format='json')

    def test_authentication_required_for_study_plan_list(self):
        response = self.client.get(reverse('study-plan-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_can_create_study_plan(self):
        response = self.create_plan_via_api()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(StudyPlan.objects.filter(user=self.user).count(), 1)
        self.assertEqual(response.data['generation_type'], StudyPlan.GenerationType.MANUAL)

    def test_creating_plan_generates_tasks(self):
        response = self.create_plan_via_api()
        plan = StudyPlan.objects.get(user=self.user)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertGreater(plan.tasks.count(), 0)
        self.assertEqual(len(response.data['tasks']), plan.tasks.count())

    def test_user_does_not_see_other_user_plans(self):
        StudyPlan.objects.create(
            user=self.other_user,
            title='Private Plan',
            subject=self.subject,
            start_date=timezone.localdate(),
            end_date=timezone.localdate(),
            daily_study_minutes=60,
            difficulty_level=StudyPlan.DifficultyLevel.MEDIUM,
            generation_type=StudyPlan.GenerationType.MANUAL,
        )

        self.authenticate()
        response = self.client.get(reverse('study-plan-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_today_endpoint_returns_today_tasks(self):
        create_response = self.create_plan_via_api(
            start_date=str(timezone.localdate()),
            end_date=str(timezone.localdate()),
            daily_study_minutes=60,
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        response = self.client.get(reverse('study-plan-today'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['date'], str(timezone.localdate()))
        self.assertGreaterEqual(len(response.data['tasks']), 1)

    def test_complete_task_updates_completion_percentage(self):
        today = timezone.localdate()
        plan = StudyPlan.objects.create(
            user=self.user,
            title='One Day Plan',
            subject=self.subject,
            start_date=today,
            end_date=today,
            daily_study_minutes=60,
            difficulty_level=StudyPlan.DifficultyLevel.MEDIUM,
            generation_type=StudyPlan.GenerationType.MANUAL,
        )
        task = StudyTask.objects.create(
            plan=plan,
            title='Review math',
            task_date=today,
            estimated_minutes=60,
            priority=StudyTask.Priority.MEDIUM,
            order=1,
        )

        self.authenticate()
        response = self.client.post(reverse('study-task-complete', args=[task.id]))

        plan.refresh_from_db()
        task.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(task.status, StudyTask.Status.COMPLETED)
        self.assertEqual(plan.completion_percentage, Decimal('100.00'))

    def test_skip_task_endpoint_works(self):
        today = timezone.localdate()
        plan = StudyPlan.objects.create(
            user=self.user,
            title='Skip Plan',
            subject=self.subject,
            start_date=today,
            end_date=today,
            daily_study_minutes=60,
            difficulty_level=StudyPlan.DifficultyLevel.MEDIUM,
            generation_type=StudyPlan.GenerationType.MANUAL,
        )
        task = StudyTask.objects.create(
            plan=plan,
            title='Skip this task',
            task_date=today,
            estimated_minutes=60,
            order=1,
        )

        self.authenticate()
        response = self.client.post(reverse('study-task-skip', args=[task.id]))
        task.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(task.status, StudyTask.Status.SKIPPED)

    def test_reopen_task_endpoint_works(self):
        today = timezone.localdate()
        plan = StudyPlan.objects.create(
            user=self.user,
            title='Reopen Plan',
            subject=self.subject,
            start_date=today,
            end_date=today,
            daily_study_minutes=60,
            difficulty_level=StudyPlan.DifficultyLevel.MEDIUM,
            generation_type=StudyPlan.GenerationType.MANUAL,
        )
        task = StudyTask.objects.create(
            plan=plan,
            title='Completed task',
            task_date=today,
            estimated_minutes=60,
            status=StudyTask.Status.COMPLETED,
            order=1,
        )

        self.authenticate()
        response = self.client.post(reverse('study-task-reopen', args=[task.id]))
        task.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(task.status, StudyTask.Status.PENDING)
        self.assertIsNone(task.completed_at)

    def test_filters_support_status_and_subject(self):
        today = timezone.localdate()
        StudyPlan.objects.create(
            user=self.user,
            title='Cancelled Math Plan',
            subject=self.subject,
            start_date=today,
            end_date=today,
            daily_study_minutes=60,
            difficulty_level=StudyPlan.DifficultyLevel.MEDIUM,
            generation_type=StudyPlan.GenerationType.MANUAL,
            status=StudyPlan.Status.CANCELLED,
        )
        StudyPlan.objects.create(
            user=self.user,
            title='Active Physics Plan',
            subject=self.other_subject,
            start_date=today,
            end_date=today,
            daily_study_minutes=60,
            difficulty_level=StudyPlan.DifficultyLevel.MEDIUM,
            generation_type=StudyPlan.GenerationType.MANUAL,
            status=StudyPlan.Status.ACTIVE,
        )

        self.authenticate()
        response = self.client.get(
            reverse('study-plan-list'),
            {'status': StudyPlan.Status.CANCELLED, 'subject': self.subject.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Cancelled Math Plan')

    def test_schema_endpoint_still_works(self):
        admin = User.objects.create_superuser(
            email='schema-admin@example.com',
            password='StrongPass123',
            full_name='Schema Admin',
        )
        self.authenticate(admin)
        response = self.client.get(reverse('api-schema'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
