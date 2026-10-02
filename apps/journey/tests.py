from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.admin_dashboard.services import assign_roles_to_user, seed_default_rbac
from apps.organizations.models import ClassMembership, Classroom, Organization
from apps.subjects.models import EducationStage, Subject

from .models import (
    ChallengeProgress,
    ClassGoal,
    PathStation,
    StationCompletion,
    SubjectPath,
    Unlockable,
    WeeklyChallenge,
)
from .services import complete_station, record_challenge_progress, total_gems

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class JourneyTests(APITestCase):
    """رحلة برّاق: sequential stations, gems, unlockables and challenges."""

    def setUp(self):
        _, self.roles = seed_default_rbac()
        self.super_admin = User.objects.create_superuser(email='j-super@example.com', password='StrongPass123', full_name='Super')
        assign_roles_to_user(self.super_admin, [self.roles['super_admin']], self.super_admin, scopes=[{'scope_type': 'global'}])
        self.content_manager = User.objects.create_user(
            email='content@example.com', password='StrongPass123', full_name='Content', role=User.Roles.ADMIN
        )
        assign_roles_to_user(self.content_manager, [self.roles['content_manager']], self.super_admin, scopes=[{'scope_type': 'global'}])

        stage = EducationStage.objects.create(name='Secondary', order=1)
        self.physics = Subject.objects.create(name='Physics', education_stage=stage, grade_level='10')
        self.path = SubjectPath.objects.create(subject=self.physics)
        self.s1 = PathStation.objects.create(path=self.path, title='Motion', order=1, gem_reward=10)
        self.s2 = PathStation.objects.create(path=self.path, title='Forces', order=2, gem_reward=15)

        self.alice = User.objects.create_user(email='alice@example.com', password='StrongPass123', full_name='Alice')
        self.client.force_authenticate(user=self.alice)

    # -- sequential stations + gems -----------------------------------------
    def test_second_station_is_locked_until_the_first_is_done(self):
        response = self.client.post(reverse('journey-complete-station', args=[self.s2.pk]), {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(StationCompletion.objects.exists())

    def test_completing_stations_in_order_awards_gems_and_advances(self):
        first = self.client.post(reverse('journey-complete-station', args=[self.s1.pk]), {}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        subject_row = next(row for row in first.data['subjects'] if row['subject'] == self.physics.pk)
        self.assertEqual(subject_row['gem_fragments_total'], 10)
        self.assertEqual(
            [(s['id'], s['completed'], s['unlocked']) for s in subject_row['stations']],
            [(self.s1.pk, True, True), (self.s2.pk, False, True)],
        )

        second = self.client.post(reverse('journey-complete-station', args=[self.s2.pk]), {}, format='json')
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(total_gems(self.alice), 25)

    def test_completing_a_station_twice_does_not_double_award(self):
        complete_station(self.alice, self.s1)
        complete_station(self.alice, self.s1)
        self.assertEqual(total_gems(self.alice), 10)
        self.assertEqual(StationCompletion.objects.filter(user=self.alice, station=self.s1).count(), 1)

    def test_journey_home_shows_locked_stations_before_any_progress(self):
        response = self.client.get(reverse('journey-home'))
        self.assertEqual(response.data['total_gems'], 0)
        row = response.data['subjects'][0]
        self.assertEqual([s['unlocked'] for s in row['stations']], [True, False])

    # -- unlockables ----------------------------------------------------------
    def test_reaching_the_gem_requirement_unlocks_an_item(self):
        Unlockable.objects.create(code='calm-theme', name='Calm theme', category='desk_theme', gem_requirement=10)
        Unlockable.objects.create(code='fire-theme', name='Fire theme', category='desk_theme', gem_requirement=100)

        complete_station(self.alice, self.s1)  # 10 gems

        response = self.client.get(reverse('journey-unlockables'))
        by_code = {row['code']: row['unlocked'] for row in response.data}
        self.assertEqual(by_code, {'calm-theme': True, 'fire-theme': False})

    # -- weekly challenges --------------------------------------------------------
    def test_a_challenge_completes_once_and_awards_gems_once(self):
        today = timezone.localdate()
        week_start = today - timezone.timedelta(days=today.weekday())
        challenge = WeeklyChallenge.objects.create(
            code='fix-mistakes', title='Fix 3 mistakes', week_start=week_start, target_value=3, gem_reward=20
        )

        record_challenge_progress(self.alice, challenge)
        record_challenge_progress(self.alice, challenge)
        record_challenge_progress(self.alice, challenge)
        record_challenge_progress(self.alice, challenge)  # past target: still a no-op

        row = ChallengeProgress.objects.get(user=self.alice, challenge=challenge)
        self.assertTrue(row.is_completed)
        self.assertEqual(row.progress_value, 3)
        self.assertEqual(total_gems(self.alice), 20)

        response = self.client.get(reverse('journey-challenges'))
        self.assertEqual(response.data[0]['my_progress']['is_completed'], True)

    def test_a_different_week_challenge_does_not_show_up_today(self):
        WeeklyChallenge.objects.create(
            code='old', title='Last week', week_start=timezone.localdate() - timezone.timedelta(days=14), target_value=1
        )
        response = self.client.get(reverse('journey-challenges'))
        self.assertEqual(response.data, [])

    # -- content authoring (journey.view / journey.manage) -----------------------
    def test_content_manager_authors_a_path_and_stations(self):
        self.client.force_authenticate(user=self.content_manager)
        chemistry = Subject.objects.create(name='Chemistry', education_stage=self.physics.education_stage, grade_level='10')

        path = self.client.post(reverse('admin-subject-path-list'), {'subject': chemistry.pk}, format='json')
        self.assertEqual(path.status_code, status.HTTP_201_CREATED, path.data)

        station = self.client.post(
            reverse('admin-path-station-list'), {'path': path.data['id'], 'title': 'Atoms', 'order': 1}, format='json'
        )
        self.assertEqual(station.status_code, status.HTTP_201_CREATED, station.data)

    def test_role_without_journey_permission_is_denied(self):
        finance = User.objects.create_user(email='finance@example.com', password='StrongPass123', full_name='F', role=User.Roles.ADMIN)
        assign_roles_to_user(finance, [self.roles['finance']], self.super_admin, scopes=[{'scope_type': 'global'}])
        self.client.force_authenticate(user=finance)
        response = self.client.get(reverse('admin-subject-path-list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -- class goals (reuses class_work permissions and scoping) -----------------
    def test_teacher_sets_a_class_goal_and_its_students_see_it(self):
        org = Organization.objects.create(name='School', created_by=self.super_admin)
        classroom = Classroom.objects.create(organization=org, name='10-A')
        teacher = User.objects.create_user(email='t@example.com', password='StrongPass123', full_name='T', role=User.Roles.ADMIN)
        assign_roles_to_user(teacher, [self.roles['class_supervisor']], self.super_admin, scopes=[{'scope_type': 'class', 'classroom': classroom}])
        ClassMembership.objects.create(classroom=classroom, user=self.alice, status='active')

        self.client.force_authenticate(user=teacher)
        created = self.client.post(
            reverse('admin-class-goal-list'), {'classroom': str(classroom.public_id), 'title': 'Finish unit 3', 'target_value': 25}, format='json'
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)

        self.client.force_authenticate(user=self.alice)
        mine = self.client.get(reverse('class-goal-list'))
        rows = mine.data['results'] if isinstance(mine.data, dict) and 'results' in mine.data else mine.data
        self.assertEqual([row['title'] for row in rows], ['Finish unit 3'])

    def test_class_goal_auto_completes_when_progress_reaches_target(self):
        org = Organization.objects.create(name='School', created_by=self.super_admin)
        classroom = Classroom.objects.create(organization=org, name='10-A')
        goal = ClassGoal.objects.create(classroom=classroom, title='x', target_value=10, progress_value=5)
        manager = User.objects.create_user(email='manager@example.com', password='StrongPass123', full_name='M', role=User.Roles.ADMIN)
        assign_roles_to_user(
            manager, [self.roles['organization_manager']], self.super_admin,
            scopes=[{'scope_type': 'organization', 'organization': org}],
        )
        self.client.force_authenticate(user=manager)

        response = self.client.patch(reverse('admin-class-goal-detail', args=[goal.pk]), {'progress_value': 10}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data['is_completed'])

    def test_a_student_outside_the_class_does_not_see_its_goal(self):
        org = Organization.objects.create(name='School', created_by=self.super_admin)
        classroom = Classroom.objects.create(organization=org, name='10-A')
        ClassGoal.objects.create(classroom=classroom, title='x', target_value=10)
        response = self.client.get(reverse('class-goal-list'))
        rows = response.data['results'] if isinstance(response.data, dict) and 'results' in response.data else response.data
        self.assertEqual(rows, [])
