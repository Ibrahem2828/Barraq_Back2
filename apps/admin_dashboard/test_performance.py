from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.ai_integration.models import AIJob
from apps.organizations.models import ClassMembership, Classroom, Organization, OrganizationMembership
from apps.quizzes.models import AttemptStatusChoices, Quiz, QuizAttempt
from apps.subjects.models import EducationStage, Subject

from .services import assign_roles_to_user, seed_default_rbac

User = get_user_model()
GLOBAL_SCOPES = [{'scope_type': 'global'}]


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class StudentPerformanceTests(APITestCase):
    """GET /admin/student-performance/ -- a supervisor's view of its students."""

    def setUp(self):
        _, self.roles = seed_default_rbac()
        self.super_admin = User.objects.create_superuser(
            email='perf-super@example.com', password='StrongPass123', full_name='Super'
        )
        assign_roles_to_user(self.super_admin, [self.roles['super_admin']], self.super_admin, scopes=GLOBAL_SCOPES)
        stage = EducationStage.objects.create(name='Secondary', order=1)
        self.subject = Subject.objects.create(name='Physics', education_stage=stage, grade_level='12')
        self.org_a = Organization.objects.create(name='School A', created_by=self.super_admin)
        self.org_b = Organization.objects.create(name='School B', created_by=self.super_admin)
        self.class_a1 = Classroom.objects.create(organization=self.org_a, name='10-A')
        self.class_a2 = Classroom.objects.create(organization=self.org_a, name='10-B')

        self.alice = self._student('alice@example.com', 'Alice', self.org_a, self.class_a1)
        self.bob = self._student('bob@example.com', 'Bob', self.org_a, self.class_a2)
        self.carol = self._student('carol@example.com', 'Carol', self.org_b)
        self.loner = User.objects.create_user(email='loner@example.com', password='StrongPass123', full_name='Loner')
        self.teacher = User.objects.create_user(
            email='teacher@example.com', password='StrongPass123', full_name='Teacher'
        )
        OrganizationMembership.objects.create(
            organization=self.org_a,
            user=self.teacher,
            member_type=OrganizationMembership.MemberType.STAFF,
            status=OrganizationMembership.Status.ACTIVE,
        )

        self.manager_a = self._admin('manager-a@example.com', 'organization_manager', {
            'scope_type': 'organization', 'organization': self.org_a,
        })
        self.supervisor_a1 = self._admin('supervisor@example.com', 'class_supervisor', {
            'scope_type': 'class', 'classroom': self.class_a1,
        })

        self._attempt(self.alice, 80)
        self._attempt(self.alice, 90)
        self._attempt(self.bob, 30)
        self._attempt(self.carol, 100)
        self._attempt(self.alice, 10, days_ago=90)  # outside the default window
        self._job(self.alice, AIJob.Character.FAHES, AIJob.Status.COMPLETED)
        self._job(self.alice, AIJob.Character.KHOLASA, AIJob.Status.FAILED)
        self._job(self.carol, AIJob.Character.RASHEED, AIJob.Status.COMPLETED)

    # -- fixtures ---------------------------------------------------------
    def _student(self, email, name, organization, classroom=None):
        user = User.objects.create_user(email=email, password='StrongPass123', full_name=name)
        OrganizationMembership.objects.create(
            organization=organization,
            user=user,
            member_type=OrganizationMembership.MemberType.STUDENT,
            status=OrganizationMembership.Status.ACTIVE,
        )
        if classroom is not None:
            ClassMembership.objects.create(classroom=classroom, user=user, status=ClassMembership.Status.ACTIVE)
        return user

    def _admin(self, email, role_code, scope):
        user = User.objects.create_user(email=email, password='StrongPass123', full_name=email, role=User.Roles.ADMIN)
        assign_roles_to_user(user, [self.roles[role_code]], self.super_admin, scopes=[scope])
        return user

    def _attempt(self, user, percentage, days_ago=1):
        quiz = Quiz.objects.create(user=user, subject=self.subject, title=f'Quiz {percentage}', topic='t')
        submitted = timezone.now() - timedelta(days=days_ago)
        return QuizAttempt.objects.create(
            user=user,
            quiz=quiz,
            status=AttemptStatusChoices.SUBMITTED,
            submitted_at=submitted,
            score=Decimal(percentage),
            max_score=Decimal(100),
            percentage=Decimal(percentage),
            correct_answers_count=percentage // 10,
            wrong_answers_count=10 - percentage // 10,
        )

    def _job(self, user, character, job_status):
        task = {
            AIJob.Character.FAHES: AIJob.TaskType.FAHES_GENERATE_QUIZ,
            AIJob.Character.KHOLASA: AIJob.TaskType.KHOLASA_GENERATE_SUMMARY,
            AIJob.Character.RASHEED: AIJob.TaskType.RASHEED_RECOMMENDATIONS,
        }[character]
        return AIJob.objects.create(
            user=user,
            character=character,
            task_type=task,
            status=job_status,
            idempotency_key=f'perf-{user.pk}-{AIJob.objects.count()}',
            input_payload={'question': 'private student text'},
            # A completed job must point at what it materialized.
            result_type='result' if job_status == AIJob.Status.COMPLETED else '',
            result_id='1' if job_status == AIJob.Status.COMPLETED else '',
        )

    def get(self, actor, name='admin-student-performance-list', args=None, **params):
        self.client.force_authenticate(user=actor)
        return self.client.get(reverse(name, args=args), params)

    @staticmethod
    def rows(response):
        data = response.data
        return data['results'] if isinstance(data, dict) and 'results' in data else data

    # -- reach ------------------------------------------------------------
    def test_organization_manager_sees_only_its_own_students(self):
        response = self.get(self.manager_a)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        emails = {row['email'] for row in self.rows(response)}
        # Not the other school's student, the independent learner, or staff.
        self.assertEqual(emails, {'alice@example.com', 'bob@example.com'})

    def test_class_supervisor_sees_only_its_class(self):
        response = self.get(self.supervisor_a1)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual([row['email'] for row in self.rows(response)], ['alice@example.com'])

    def test_platform_admin_sees_every_organization_student(self):
        response = self.get(self.super_admin)

        emails = {row['email'] for row in self.rows(response)}
        self.assertEqual(emails, {'alice@example.com', 'bob@example.com', 'carol@example.com'})

    def test_filters_narrow_to_an_organization_or_a_class(self):
        by_org = self.get(self.super_admin, organization=str(self.org_b.public_id))
        by_class = self.get(self.manager_a, classroom=str(self.class_a2.public_id))

        self.assertEqual([row['email'] for row in self.rows(by_org)], ['carol@example.com'])
        self.assertEqual([row['email'] for row in self.rows(by_class)], ['bob@example.com'])

    def test_an_unreachable_organization_or_class_is_not_found(self):
        other_org = self.get(self.manager_a, organization=str(self.org_b.public_id))
        sibling_class = self.get(self.supervisor_a1, classroom=str(self.class_a2.public_id))

        self.assertEqual(other_org.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(sibling_class.status_code, status.HTTP_404_NOT_FOUND)

    def test_role_without_students_view_is_denied(self):
        finance = self._admin('finance@example.com', 'finance', {'scope_type': 'global'})

        response = self.get(finance)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_student_is_denied(self):
        response = self.get(self.alice)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -- metrics ----------------------------------------------------------
    def test_row_metrics_cover_the_reporting_window(self):
        response = self.get(self.manager_a, ordering='name')
        alice, bob = self.rows(response)

        self.assertEqual(alice['quizzes_submitted'], 2)
        self.assertEqual(alice['average_score'], 85.0)
        self.assertEqual(alice['ai_requests'], 2)
        self.assertEqual(alice['ai_completed'], 1)
        self.assertEqual(alice['ai_failed'], 1)
        self.assertEqual(alice['ai_by_character']['fahes'], 1)
        self.assertEqual(alice['ai_by_character']['kholasa'], 1)
        self.assertEqual([c['name'] for c in alice['classes']], ['10-A'])
        self.assertFalse(alice['needs_attention'])
        self.assertEqual(bob['average_score'], 30.0)
        self.assertTrue(bob['needs_attention'])

    def test_a_longer_window_includes_older_attempts(self):
        response = self.get(self.manager_a, ordering='name', days=120)

        alice = self.rows(response)[0]
        self.assertEqual(alice['quizzes_submitted'], 3)
        self.assertAlmostEqual(alice['average_score'], 60.0)

    def test_ordering_by_score(self):
        response = self.get(self.super_admin, ordering='-score')

        self.assertEqual(
            [row['email'] for row in self.rows(response)],
            ['carol@example.com', 'alice@example.com', 'bob@example.com'],
        )

    def test_unknown_ordering_is_rejected(self):
        response = self.get(self.super_admin, ordering='password')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_summary_aggregates_the_scoped_population(self):
        response = self.get(self.manager_a, name='admin-student-performance-summary')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['students_count'], 2)
        self.assertEqual(response.data['active_students'], 2)
        self.assertEqual(response.data['needs_attention'], 1)
        self.assertEqual(response.data['quizzes_submitted'], 3)
        self.assertAlmostEqual(response.data['average_score'], 66.7)
        self.assertEqual(response.data['ai_requests'], 2)
        self.assertEqual(response.data['ai_by_character']['rasheed'], 0)  # Carol is not theirs
        self.assertEqual(response.data['score_distribution']['85_plus'], 1)
        self.assertEqual(response.data['score_distribution']['below_50'], 1)

    def test_detail_lists_recent_work_without_ai_content(self):
        response = self.get(self.manager_a, name='admin-student-performance-detail', args=[self.alice.pk])

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data['recent_attempts']), 2)
        # Newest first; the 90-day-old attempt is outside the window.
        self.assertEqual([a['percentage'] for a in response.data['recent_attempts']], [90.0, 80.0])
        activity = response.data['recent_ai_activity']
        self.assertEqual({item['character'] for item in activity}, {'fahes', 'kholasa'})
        self.assertNotIn('private student text', str(response.data))

    def test_detail_of_a_student_outside_scope_is_not_found(self):
        response = self.get(self.manager_a, name='admin-student-performance-detail', args=[self.carol.pk])
        loner = self.get(self.super_admin, name='admin-student-performance-detail', args=[self.loner.pk])

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(loner.status_code, status.HTTP_404_NOT_FOUND)
