import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.admin_dashboard.models import AuditLog
from apps.admin_dashboard.services import assign_roles_to_user, seed_default_rbac
from apps.ai_integration.models import AIJob
from apps.sources.models import StudentSource, StudentSourceCollection, StudentSourceInteraction
from apps.subjects.models import EducationStage, Subject

from .exceptions import SubscriptionFeatureNotAllowed, SubscriptionLimitExceeded
from .models import SubscriptionPlan, SubscriptionUsage, UsageLedgerEntry, UserSubscription
from .services import (
    can_create_collection,
    can_upload_source,
    can_use_character,
    consume_character_request,
    ensure_default_plans,
    get_or_create_user_subscription,
    get_remaining_limits,
    get_user_subscription,
    refund_character_request,
    reserve_character_request,
)

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class SubscriptionsTestCase(APITestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        _, self.admin_roles = seed_default_rbac()
        self.plans = ensure_default_plans()
        self.student = User.objects.create_user(
            email='sub-student@example.com',
            password='StrongPass123',
            full_name='Subscription Student',
        )
        self.super_admin = User.objects.create_superuser(
            email='sub-super@example.com',
            password='StrongPass123',
            full_name='Subscription Super',
        )
        assign_roles_to_user(self.super_admin, [self.admin_roles['super_admin']], self.super_admin)
        self.finance_admin = User.objects.create_user(
            email='finance@example.com',
            password='StrongPass123',
            full_name='Finance Admin',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        assign_roles_to_user(self.finance_admin, [self.admin_roles['finance']], self.super_admin)
        self.basic_admin = User.objects.create_user(
            email='basic-admin@example.com',
            password='StrongPass123',
            full_name='Basic Admin',
            role=User.Roles.ADMIN,
            is_staff=True,
        )
        assign_roles_to_user(self.basic_admin, [self.admin_roles['admin']], self.super_admin)
        self.stage = EducationStage.objects.create(name='Secondary', order=1)
        self.subject = Subject.objects.create(
            name='Mathematics',
            education_stage=self.stage,
            grade_level='12',
        )

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def create_source(self, user=None, size=100, title='Source'):
        return StudentSource.objects.create(
            user=user or self.student,
            subject=self.subject,
            title=title,
            source_type=StudentSource.SourceType.TEXT,
            file=SimpleUploadedFile('source.txt', b'a' * size, content_type='text/plain'),
            original_filename='source.txt',
            file_size=size,
            mime_type='text/plain',
            extension='txt',
            extracted_text='This is a useful source text for testing.',
            status=StudentSource.Status.READY,
        )

    def test_default_plans_created(self):
        self.assertTrue(SubscriptionPlan.objects.filter(code='free').exists())
        self.assertTrue(SubscriptionPlan.objects.filter(code='premium').exists())
        self.assertTrue(SubscriptionPlan.objects.filter(code='pro').exists())
        self.assertTrue(SubscriptionPlan.objects.filter(code='school').exists())

    def test_get_user_subscription_returns_free_fallback(self):
        UserSubscription.all_objects.filter(user=self.student).hard_delete()
        subscription = get_user_subscription(self.student)

        self.assertEqual(subscription.plan.code, 'free')

    def test_delete_soft_deletes_subscription_row(self):
        subscription = UserSubscription.objects.get(user=self.student)
        subscription_id = subscription.id

        UserSubscription.objects.filter(user=self.student).delete()

        self.assertFalse(UserSubscription.objects.filter(id=subscription_id).exists())
        self.assertTrue(UserSubscription.all_objects.get(id=subscription_id).is_deleted)

    def test_get_or_create_revives_a_soft_deleted_subscription_instead_of_erroring(self):
        original = UserSubscription.objects.get(user=self.student)
        original_id = original.id
        UserSubscription.objects.filter(user=self.student).delete()

        revived = get_or_create_user_subscription(self.student)

        self.assertEqual(revived.id, original_id)
        self.assertFalse(revived.is_deleted)
        self.assertEqual(revived.plan.code, 'free')

    def test_new_user_gets_free_subscription(self):
        user = User.objects.create_user(
            email='new-sub-user@example.com',
            password='StrongPass123',
            full_name='New User',
        )

        self.assertEqual(user.subscription.plan.code, 'free')

    def test_can_create_collection_respects_limit(self):
        for index in range(3):
            StudentSourceCollection.objects.create(user=self.student, name=f'Folder {index}')

        with self.assertRaises(SubscriptionLimitExceeded):
            can_create_collection(self.student)

    def test_can_upload_source_respects_max_sources(self):
        free = self.plans['free']
        free.limits = {**free.limits, 'max_sources': 0}
        free.save(update_fields=['limits', 'updated_at'])

        with self.assertRaises(SubscriptionLimitExceeded):
            can_upload_source(self.student, 100)

    def test_can_upload_source_respects_max_file_size(self):
        free = self.plans['free']
        free.limits = {**free.limits, 'max_file_size_mb': 1}
        free.save(update_fields=['limits', 'updated_at'])

        with self.assertRaises(SubscriptionLimitExceeded):
            can_upload_source(self.student, 2 * 1024 * 1024)

    def test_can_use_character_respects_feature_flags(self):
        with self.assertRaises(SubscriptionFeatureNotAllowed):
            can_use_character(self.student, StudentSourceInteraction.Character.KHOLASA)

    def test_consume_character_request_increments_usage(self):
        consume_character_request(self.student, StudentSourceInteraction.Character.RASHEED)
        usage = SubscriptionUsage.objects.get(user=self.student)

        self.assertEqual(usage.rasheed_requests, 1)
        self.assertEqual(usage.ai_requests_used, 1)

    def test_remaining_limits_correct(self):
        StudentSourceCollection.objects.create(user=self.student, name='Folder')
        remaining = get_remaining_limits(self.student)

        self.assertEqual(remaining['max_collections'], 2)

    def test_authenticated_user_can_get_subscription_me(self):
        self.authenticate(self.student)
        response = self.client.get(reverse('my-subscription'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['plan']['code'], 'free')

    def test_unauthenticated_cannot_get_subscription_me(self):
        response = self.client.get(reverse('my-subscription'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_plans_endpoint_returns_public_active_plans(self):
        self.authenticate(self.student)
        response = self.client.get(reverse('subscription-plan-public-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        codes = {item['code'] for item in response.data['results']}
        self.assertIn('free', codes)
        self.assertNotIn('school', codes)

    def test_free_user_cannot_create_more_than_max_collections(self):
        for index in range(3):
            StudentSourceCollection.objects.create(user=self.student, name=f'Folder {index}')
        self.authenticate(self.student)
        response = self.client.post(
            reverse('student-source-collection-list'),
            {'name': 'Blocked Folder'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_free_user_cannot_upload_file_larger_than_plan_limit(self):
        free = self.plans['free']
        free.limits = {**free.limits, 'max_file_size_mb': 1}
        free.save(update_fields=['limits', 'updated_at'])
        self.authenticate(self.student)
        response = self.client.post(
            reverse('student-source-list'),
            {
                'title': 'Large File',
                'subject': self.subject.id,
                'file': SimpleUploadedFile('large.txt', b'a' * (2 * 1024 * 1024), content_type='text/plain'),
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unavailable_kholasa_is_rejected_without_usage(self):
        source = self.create_source()
        self.authenticate(self.student)
        response = self.client.post(reverse('student-source-use-with-kholasa', args=[source.id]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        usage = SubscriptionUsage.objects.filter(user=self.student).first()
        self.assertTrue(usage is None or usage.kholasa_requests == 0)

    def test_allowed_character_usage_increments_usage(self):
        source = self.create_source()
        self.authenticate(self.student)
        response = self.client.post(reverse('student-source-use-with-rasheed', args=[source.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(SubscriptionUsage.objects.get(user=self.student).rasheed_requests, 1)

    def test_admin_with_subscription_plan_view_can_list_plans(self):
        self.authenticate(self.finance_admin)
        response = self.client.get(reverse('admin-subscription-plan-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_without_subscription_permission_cannot_list_plans(self):
        self.authenticate(self.basic_admin)
        response = self.client.get(reverse('admin-subscription-plan-list'))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_super_admin_can_create_plan(self):
        self.authenticate(self.super_admin)
        response = self.client.post(
            reverse('admin-subscription-plan-list'),
            {
                'code': 'test_plan',
                'name': 'Test Plan',
                'description': 'Test',
                'price': '1.00',
                'currency': 'USD',
                'billing_interval': 'monthly',
                'features': {},
                'limits': {},
                'is_active': True,
                'is_public': False,
                'sort_order': 99,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_admin_with_subscription_update_can_change_user_subscription(self):
        self.authenticate(self.finance_admin)
        response = self.client.post(
            reverse('admin-managed-user-change-subscription', args=[self.student.id]),
            {'plan_code': 'premium'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.student.refresh_from_db()
        self.assertEqual(self.student.subscription.plan.code, 'premium')
        self.assertTrue(AuditLog.objects.filter(action='subscription.updated').exists())

    def test_admin_overview_includes_subscription_counts(self):
        self.authenticate(self.super_admin)
        response = self.client.get(reverse('admin-overview'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('subscriptions_count', response.data)
        self.assertIn('subscriptions_by_plan', response.data)

    def test_admin_me_allowed_sections_includes_subscriptions(self):
        self.authenticate(self.finance_admin)
        response = self.client.get(reverse('admin-me'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['allowed_sections']['subscriptions'])
        self.assertTrue(response.data['allowed_sections']['subscription_plans'])

    def test_old_health_login_and_sources_still_work(self):
        health = self.client.get(reverse('health-check'))
        login = self.client.post(
            reverse('login'),
            {'email': self.student.email, 'password': 'StrongPass123'},
            format='json',
        )
        self.authenticate(self.student)
        collection = self.client.post(
            reverse('student-source-collection-list'),
            {'name': 'Old Flow Folder'},
            format='json',
        )

        self.assertEqual(health.status_code, status.HTTP_200_OK)
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertEqual(collection.status_code, status.HTTP_201_CREATED)

    def test_usage_ledger_reserve_and_refund_are_idempotent(self):
        job = AIJob.objects.create(
            user=self.student,
            character=AIJob.Character.FAHES,
            task_type=AIJob.TaskType.FAHES_GENERATE_QUIZ,
            status=AIJob.Status.QUEUED,
            idempotency_key='usage-ledger-idempotency',
        )

        reserve_character_request(self.student, job.character, job=job)
        reserve_character_request(self.student, job.character, job=job)
        usage = SubscriptionUsage.objects.get(user=self.student)
        self.assertEqual(usage.ai_requests_used, 1)
        self.assertEqual(usage.fahes_requests, 1)
        self.assertEqual(
            UsageLedgerEntry.objects.filter(
                user=self.student,
                operation=UsageLedgerEntry.Operation.RESERVE,
                idempotency_key=job.idempotency_key,
            ).count(),
            1,
        )

        refund_character_request(self.student, job.character, job=job)
        refund_character_request(self.student, job.character, job=job)
        usage.refresh_from_db()
        self.assertEqual(usage.ai_requests_used, 0)
        self.assertEqual(usage.fahes_requests, 0)
        self.assertEqual(
            UsageLedgerEntry.objects.filter(
                user=self.student,
                operation=UsageLedgerEntry.Operation.REFUND,
                idempotency_key=job.idempotency_key,
            ).count(),
            1,
        )
