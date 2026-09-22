import tempfile

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.ai_integration.models import AIJob
from apps.sources.models import StudentSourceCollection
from apps.subjects.models import EducationStage, Subject

from .models import AdminPermission, AdminRole, AdminUserRole, AuditLog
from .services import (
    assign_roles_to_user,
    get_user_admin_permissions,
    seed_default_rbac,
    user_has_admin_permission,
)

User = get_user_model()
GLOBAL_SCOPES = [{'scope_type': 'global'}]


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class AdminDashboardAPITestCase(APITestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        _, self.roles = seed_default_rbac()
        self.super_admin = User.objects.create_superuser(
            email='super@example.com',
            password='StrongPass123',
            full_name='Super Admin',
        )
        assign_roles_to_user(self.super_admin, [self.roles['super_admin']], self.super_admin, scopes=GLOBAL_SCOPES)
        self.project_admin = User.objects.create_user(
            email='project@example.com',
            password='StrongPass123',
            full_name='Project Admin',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        assign_roles_to_user(self.project_admin, [self.roles['admin']], self.super_admin, scopes=GLOBAL_SCOPES)
        self.student = User.objects.create_user(
            email='student-admin-tests@example.com',
            password='StrongPass123',
            full_name='Student User',
        )
        self.empty_role = AdminRole.objects.create(
            name='No Dashboard',
            code='no_dashboard',
            is_active=True,
        )
        self.limited_admin = User.objects.create_user(
            email='limited@example.com',
            password='StrongPass123',
            full_name='Limited Admin',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        assign_roles_to_user(self.limited_admin, [self.empty_role], self.super_admin, scopes=GLOBAL_SCOPES)

    def tearDown(self):
        self.override.disable()

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_student_cannot_access_admin_me(self):
        self.authenticate(self.student)
        response = self.client.get(reverse('admin-me'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_access_admin_api(self):
        response = self.client.get(reverse('admin-overview'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_super_admin_can_access_overview(self):
        self.authenticate(self.super_admin)
        response = self.client.get(reverse('admin-overview'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('users_count', response.data)

    def test_admin_without_dashboard_permission_cannot_access_overview(self):
        self.authenticate(self.limited_admin)
        response = self.client.get(reverse('admin-overview'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_seed_creates_permissions_and_roles(self):
        self.assertTrue(AdminPermission.objects.filter(code='dashboard.view').exists())
        self.assertTrue(AdminRole.objects.filter(code='super_admin').exists())

    def test_super_admin_has_all_permissions(self):
        permission_count = AdminPermission.objects.count()

        self.assertEqual(len(get_user_admin_permissions(self.super_admin)), permission_count)
        self.assertTrue(user_has_admin_permission(self.super_admin, 'admins.create'))

    def test_project_admin_has_limited_permissions(self):
        permissions = get_user_admin_permissions(self.project_admin)

        self.assertIn('dashboard.view', permissions)
        self.assertIn('users.view', permissions)
        self.assertNotIn('admins.create', permissions)

    def test_admin_me_returns_allowed_sections(self):
        self.authenticate(self.project_admin)
        response = self.client.get(reverse('admin-me'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['allowed_sections']['dashboard'])
        self.assertFalse(response.data['allowed_sections']['admins'])

    def test_super_admin_can_create_admin(self):
        self.authenticate(self.super_admin)
        response = self.client.post(
            reverse('admin-user-list'),
            {
                'email': 'created-admin@example.com',
                'full_name': 'Created Admin',
                'password': 'StrongPass123',
                'role_codes': ['support'],
                'scopes': GLOBAL_SCOPES,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse('password' in response.data)
        self.assertTrue(AuditLog.objects.filter(action='admin.created').exists())

    def test_admin_without_permission_cannot_create_admin(self):
        self.authenticate(self.project_admin)
        response = self.client.post(
            reverse('admin-user-list'),
            {
                'email': 'blocked-admin@example.com',
                'full_name': 'Blocked Admin',
                'password': 'StrongPass123',
                'role_codes': ['support'],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_create_super_admin_unless_super_admin(self):
        creator_role = AdminRole.objects.create(name='Creator', code='creator')
        creator_role.permissions.add(AdminPermission.objects.get(code='admins.create'))
        creator = User.objects.create_user(
            email='creator@example.com',
            password='StrongPass123',
            full_name='Creator',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        assign_roles_to_user(creator, [creator_role], self.super_admin, scopes=GLOBAL_SCOPES)
        self.authenticate(creator)

        response = self.client.post(
            reverse('admin-user-list'),
            {
                'email': 'blocked-super@example.com',
                'full_name': 'Blocked Super',
                'password': 'StrongPass123',
                'role_codes': ['super_admin'],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_delete_last_super_admin(self):
        self.authenticate(self.super_admin)
        response = self.client.delete(reverse('admin-user-detail', args=[self.super_admin.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_delete_self(self):
        other_super = User.objects.create_superuser(
            email='super2@example.com',
            password='StrongPass123',
            full_name='Other Super',
        )
        assign_roles_to_user(other_super, [self.roles['super_admin']], self.super_admin, scopes=GLOBAL_SCOPES)
        self.authenticate(self.super_admin)
        response = self.client.delete(reverse('admin-user-detail', args=[self.super_admin.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assign_role_works_with_permission(self):
        self.authenticate(self.super_admin)
        response = self.client.post(
            reverse('admin-user-assign-roles', args=[self.project_admin.id]),
            {'role_codes': ['support'], 'scopes': GLOBAL_SCOPES},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            AdminUserRole.objects.filter(
                user=self.project_admin,
                role__code='support',
                is_active=True,
            ).exists()
        )

    def test_users_list_requires_users_view(self):
        self.authenticate(self.limited_admin)
        response = self.client.get(reverse('admin-managed-user-list'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_suspend_requires_users_suspend(self):
        self.authenticate(self.project_admin)
        response = self.client.post(
            reverse('admin-managed-user-suspend',
                    args=[self.student.id])
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_audit_logs_view_permission_required(self):
        self.authenticate(self.project_admin)
        response = self.client.get(reverse('admin-audit-log-list'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_health_and_login_old_apis_still_work(self):
        health = self.client.get(reverse('health-check'))
        login = self.client.post(
            reverse('login'),
            {'email': self.student.email, 'password': 'StrongPass123'},
            format='json',
        )

        self.assertEqual(health.status_code, status.HTTP_200_OK)
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_student_sources_old_api_still_works(self):
        stage = EducationStage.objects.create(name='Secondary', order=1)
        subject = Subject.objects.create(
            name='Math',
            education_stage=stage,
            grade_level='12',
        )
        collection = StudentSourceCollection.objects.create(
            user=self.student,
            subject=subject,
            name='Math Notes',
        )
        self.authenticate(self.student)
        response = self.client.get(reverse('student-source-collection-detail', args=[collection.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_sources_admin_list_requires_permission(self):
        self.authenticate(self.project_admin)
        response = self.client.get(reverse('admin-source-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_ai_usage_requires_analytics_permission(self):
        self.authenticate(self.limited_admin)
        response = self.client.get(reverse('admin-ai-usage'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_ai_usage_aggregates_cost_and_tokens_from_completed_jobs(self):
        AIJob.objects.create(
            user=self.student,
            character=AIJob.Character.FAHES,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            status=AIJob.Status.COMPLETED,
            idempotency_key='usage-test-1',
            result_type='quiz',
            result_id='1',
            input_tokens=100,
            output_tokens=50,
            cost_usd='0.010000',
        )
        AIJob.objects.create(
            user=self.student,
            character=AIJob.Character.KHOLASA,
            task_type=AIJob.TaskType.KHOLASA_GENERATE_SUMMARY,
            status=AIJob.Status.COMPLETED,
            idempotency_key='usage-test-2',
            result_type='summary',
            result_id='1',
            input_tokens=200,
            output_tokens=80,
            cost_usd='0.025000',
        )
        AIJob.objects.create(
            user=self.student,
            character=AIJob.Character.FAHES,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            status=AIJob.Status.FAILED,
            idempotency_key='usage-test-3',
        )

        self.authenticate(self.super_admin)
        response = self.client.get(reverse('admin-ai-usage'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        totals = response.data['totals']
        self.assertEqual(totals['job_count'], 3)
        self.assertEqual(totals['completed_count'], 2)
        self.assertEqual(totals['input_tokens'], 300)
        self.assertEqual(totals['output_tokens'], 130)
        self.assertEqual(totals['total_tokens'], 430)
        self.assertEqual(float(totals['cost_usd']), 0.035)
        characters = {row['character']: row for row in response.data['by_character']}
        self.assertEqual(characters['fahes']['job_count'], 2)
        self.assertEqual(float(characters['kholasa']['cost_usd']), 0.025)


class ApplicationAccessAndAuthorizationTests(APITestCase):
    """Phase 1 authorization matrix, exercised through the API rather than
    through navigation: hiding a route is not the security mechanism."""

    def setUp(self):
        cache.clear()
        _, self.roles = seed_default_rbac()
        self.student = User.objects.create_user(
            email='student@example.com', password='StrongPass123', full_name='Student'
        )
        self.super_admin = User.objects.create_user(
            email='root@example.com',
            password='StrongPass123',
            full_name='Root',
            role=User.Roles.SUPER_ADMIN,
            is_superuser=True,
        )
        # A limited admin: support role only, so `support.view` but not
        # `roles.view`/`system.view`.
        self.limited_admin = User.objects.create_user(
            email='support@example.com',
            password='StrongPass123',
            full_name='Support',
            role=User.Roles.SUPPORT,
        )
        assign_roles_to_user(self.limited_admin, [self.roles['support']], scopes=GLOBAL_SCOPES)
        # A business-role classification with no RBAC assignment must never
        # grant dashboard access.
        self.unassigned_admin = User.objects.create_user(
            email='unassigned@example.com',
            password='StrongPass123',
            full_name='Unassigned',
            role=User.Roles.ADMIN,
        )

    # -- anonymous ---------------------------------------------------------
    def test_anonymous_cannot_reach_admin_or_student_apis(self):
        for url in (reverse('admin-me'), reverse('student-source-list')):
            with self.subTest(url=url):
                self.assertEqual(
                    self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED
                )

    # -- student -----------------------------------------------------------
    def test_student_is_granted_student_web_only(self):
        self.client.force_authenticate(self.student)

        response = self.client.get(reverse('user-me'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['allowed_apps'], ['student_web'])

    def test_student_cannot_reach_the_dashboard_api(self):
        self.client.force_authenticate(self.student)
        self.assertEqual(
            self.client.get(reverse('admin-me')).status_code, status.HTTP_403_FORBIDDEN
        )

    # -- is_staff without an assignment ------------------------------------
    def test_a_staff_flag_alone_does_not_grant_dashboard_access(self):
        """Django admin-site admission is not Baraq dashboard authority."""
        self.assertFalse(self.unassigned_admin.is_staff)
        self.unassigned_admin.is_staff = True
        self.unassigned_admin.save(update_fields=['is_staff'])
        self.client.force_authenticate(self.unassigned_admin)

        self.assertEqual(
            self.client.get(reverse('admin-me')).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_an_unassigned_admin_is_not_offered_the_dashboard_app(self):
        self.client.force_authenticate(self.unassigned_admin)
        response = self.client.get(reverse('user-me'))
        self.assertNotIn('dashboard', response.data['allowed_apps'])

    # -- limited admin -----------------------------------------------------
    def test_limited_admin_reaches_a_granted_section(self):
        self.client.force_authenticate(self.limited_admin)

        response = self.client.get(reverse('admin-me'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('dashboard', response.data['allowed_apps'])
        self.assertIn('student_web', response.data['allowed_apps'])
        self.assertTrue(response.data['allowed_sections']['support'])

    def test_limited_admin_is_denied_a_section_it_was_not_granted(self):
        """The section flag and the endpoint must agree -- and the endpoint is
        what actually protects the data."""
        self.client.force_authenticate(self.limited_admin)

        me = self.client.get(reverse('admin-me'))
        self.assertFalse(me.data['allowed_sections']['roles'])

        # A direct API call, not a hidden nav item.
        response = self.client.get(reverse('admin-role-list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_view_permission_does_not_imply_a_write_action(self):
        """users.view must not carry users.suspend."""
        self.client.force_authenticate(self.limited_admin)
        permissions_held = set(get_user_admin_permissions(self.limited_admin))

        if 'users.view' in permissions_held:
            self.assertNotIn(
                'users.suspend',
                permissions_held,
                'the support role should not carry a suspend grant',
            )
            response = self.client.post(
                reverse('admin-managed-user-suspend', args=[self.student.pk])
            )
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -- super admin -------------------------------------------------------
    def test_super_admin_keeps_every_section_and_both_apps(self):
        """Guards against the RBAC tightening silently demoting the owner."""
        self.client.force_authenticate(self.super_admin)

        response = self.client.get(reverse('admin-me'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_superuser'])
        self.assertEqual(
            sorted(response.data['allowed_apps']), ['dashboard', 'student_web']
        )
        self.assertTrue(
            all(response.data['allowed_sections'].values()),
            'super admin lost a section',
        )

    def test_super_admin_reaches_the_sections_a_limited_admin_cannot(self):
        self.client.force_authenticate(self.super_admin)
        self.assertEqual(
            self.client.get(reverse('admin-role-list')).status_code, status.HTTP_200_OK
        )

    # -- fail-closed default ----------------------------------------------
    def test_the_permission_class_denies_when_no_permission_is_declared(self):
        """A view that forgets to declare its permission must not be open."""
        from apps.admin_dashboard.permissions import HasAdminPermission

        class ViewWithoutPermission:
            required_permission = None

            def get_required_permission(self):
                return None

        request = type('R', (), {'user': self.super_admin})()
        self.assertFalse(
            HasAdminPermission().has_permission(request, ViewWithoutPermission())
        )
