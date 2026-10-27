"""Manual, payment-backed activation of paid subscriptions.

The contract under test: one verified payment reference buys exactly one
activation; the activation, the subscription change and the audit entry stand
or fall together; only holders of ``subscriptions.activate`` can do it; and no
other admin path can hand out a paid plan.
"""

import threading
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.admin_dashboard.models import AdminPermission, AuditLog
from apps.admin_dashboard.services import assign_roles_to_user, seed_default_rbac

from .activation import (
    activate_paid_subscription,
    add_months,
    looks_like_card_number,
    normalize_payment_reference,
    payment_reference_key,
    validate_payment_reference,
)
from .exceptions import SubscriptionError
from .models import SubscriptionActivation, SubscriptionPlan, UserSubscription
from .services import ensure_default_plans, get_or_create_user_subscription, get_user_plan

User = get_user_model()
HOSTS = ['testserver', 'localhost', '127.0.0.1']


class ReferenceRuleTests(TestCase):
    def test_references_are_compared_without_case_or_whitespace(self):
        self.assertEqual(payment_reference_key('  TX  9 12 '), payment_reference_key('tx912'))
        self.assertEqual(normalize_payment_reference('TX  9'), 'TX 9')

    def test_fullwidth_characters_are_normalised(self):
        self.assertEqual(payment_reference_key('ＴＸ-１２３'), payment_reference_key('tx-123'))

    def test_a_valid_card_number_is_never_a_reference(self):
        self.assertTrue(looks_like_card_number('4111 1111 1111 1111'))
        self.assertTrue(looks_like_card_number('4111-1111-1111-1111'))
        with self.assertRaises(SubscriptionError) as caught:
            validate_payment_reference('4111111111111111')
        self.assertEqual(caught.exception.domain_code, 'payment_reference_looks_like_card')

    def test_ordinary_receipt_numbers_are_accepted(self):
        for value in ('TRX-2026/10/00123', 'BANK:998877665544', 'receipt #4471', '1234567890123456'):
            # the last is 16 digits but fails Luhn: a receipt number, not a card
            self.assertEqual(validate_payment_reference(value), value)

    def test_malformed_references_are_rejected(self):
        for value in ('', '   ', 'ab', 'x' * 121, '<script>1</script>', 'ref;DROP TABLE'):
            with self.assertRaises(SubscriptionError, msg=value) as caught:
                validate_payment_reference(value)
            self.assertEqual(caught.exception.domain_code, 'invalid_payment_reference')

    def test_add_months_clamps_to_the_end_of_a_short_month(self):
        start = timezone.now().replace(year=2026, month=1, day=31)
        self.assertEqual(add_months(start, 1).day, 28)
        self.assertEqual(add_months(start, 1).month, 2)
        self.assertEqual(add_months(start, 12).year, 2027)
        self.assertEqual(add_months(start.replace(month=12), 2).month, 2)


@override_settings(ALLOWED_HOSTS=HOSTS)
class ActivationApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.roles = seed_default_rbac()
        cls.plans = ensure_default_plans()
        cls.super_admin = User.objects.create_superuser(
            email='act-super@example.com', password='StrongPass123', full_name='Super'
        )
        assign_roles_to_user(cls.super_admin, [cls.roles['super_admin']], cls.super_admin, scopes=[{'scope_type': 'global'}])
        cls.finance = cls._staff('act-finance@example.com', 'finance')
        cls.basic_admin = cls._staff('act-basic@example.com', 'admin')
        cls.support = cls._staff('act-support@example.com', 'support')
        cls.analyst = cls._staff('act-analyst@example.com', 'analyst')
        cls.student = User.objects.create_user(email='act-student@example.com', password='StrongPass123', full_name='S')
        cls.other_student = User.objects.create_user(email='act-other@example.com', password='StrongPass123', full_name='O')

    @classmethod
    def _staff(cls, email, role):
        user = User.objects.create_user(
            email=email, password='StrongPass123', full_name=role, role=User.Roles.ADMIN, is_staff=True
        )
        assign_roles_to_user(user, [cls.roles[role]], cls.super_admin, scopes=[{'scope_type': 'global'}])
        return user

    def setUp(self):
        self.client = APIClient()

    def activate(self, user=None, actor=None, **overrides):
        payload = {'plan_code': 'premium', 'payment_method': 'bank_transfer', 'payment_reference': 'TRX-1001'}
        payload.update(overrides)
        if actor is not False:
            self.client.force_authenticate(actor or self.finance)
        return self.client.post(
            reverse('admin-managed-user-activate-subscription', args=[(user or self.student).id]),
            payload,
            format='json',
        )

    # -- the happy path ------------------------------------------------------

    def test_finance_activates_a_paid_plan_with_a_full_audit_trail(self):
        before = timezone.now()
        response = self.activate()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data['created'])
        subscription = UserSubscription.objects.get(user=self.student)
        self.assertEqual(subscription.plan.code, 'premium')
        self.assertEqual(subscription.status, UserSubscription.Status.ACTIVE)
        self.assertEqual(subscription.provider, UserSubscription.Provider.MANUAL)
        self.assertFalse(subscription.auto_renew)
        self.assertGreater(subscription.current_period_end, add_months(before, 1) - timedelta(minutes=1))
        self.assertEqual(get_user_plan(self.student).code, 'premium')

        activation = SubscriptionActivation.objects.get()
        self.assertEqual(activation.user, self.student)
        self.assertEqual(activation.activated_by, self.finance)
        self.assertEqual(activation.payment_reference, 'TRX-1001')
        self.assertEqual(activation.previous_plan_code, 'free')
        self.assertEqual(activation.period_end, subscription.current_period_end)
        self.assertEqual(subscription.metadata['last_activation_id'], activation.pk)

        audit = AuditLog.objects.get(action='subscription.manually_activated')
        self.assertEqual(audit.actor, self.finance)
        self.assertEqual(audit.metadata['payment_reference'], 'TRX-1001')
        self.assertEqual(audit.metadata['activation_id'], activation.pk)
        self.assertEqual(audit.target_id, str(subscription.pk))

    def test_super_admin_may_activate(self):
        self.assertEqual(self.activate(actor=self.super_admin).status_code, status.HTTP_201_CREATED)

    def test_yearly_and_lifetime_plans_get_their_own_period(self):
        SubscriptionPlan.objects.create(
            code='yearly', name='Yearly', price=Decimal('99'), billing_interval='yearly'
        )
        SubscriptionPlan.objects.create(
            code='forever', name='Forever', price=Decimal('300'), billing_interval='lifetime'
        )
        yearly = self.activate(plan_code='yearly', payment_reference='REF-Y1').data
        self.assertEqual(
            datetime.fromisoformat(yearly['activation']['period_end']).year,
            timezone.now().year + 1,
        )
        forever = self.activate(user=self.other_student, plan_code='forever', payment_reference='REF-L1').data
        self.assertIsNone(forever['activation']['period_end'])
        self.assertTrue(get_user_plan(self.other_student).code == 'forever')

    def test_a_renewal_continues_from_the_end_of_the_running_period(self):
        first = self.activate(payment_reference='REF-R1').data['activation']['period_end']
        second = self.activate(payment_reference='REF-R2').data['activation']['period_end']

        first_end = datetime.fromisoformat(first)
        second_end = datetime.fromisoformat(second)
        self.assertEqual(second_end, add_months(first_end, 1))
        self.assertEqual(SubscriptionActivation.objects.count(), 2)

    def test_an_upgrade_starts_a_new_period_today(self):
        self.activate(payment_reference='REF-U1')
        response = self.activate(plan_code='pro', payment_reference='REF-U2')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        subscription = UserSubscription.objects.get(user=self.student)
        self.assertEqual(subscription.plan.code, 'pro')
        self.assertEqual(response.data['activation']['previous_plan_code'], 'premium')
        self.assertLess(subscription.current_period_end, add_months(timezone.now(), 1) + timedelta(minutes=1))

    def test_an_explicit_period_end_is_honoured(self):
        end = timezone.now() + timedelta(days=90)
        response = self.activate(current_period_end=end.isoformat())

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(UserSubscription.objects.get(user=self.student).current_period_end, end)

    def test_a_canceled_subscription_is_reactivated(self):
        subscription = get_or_create_user_subscription(self.student)
        subscription.status = UserSubscription.Status.CANCELED
        subscription.canceled_at = timezone.now()
        subscription.save()

        self.assertEqual(self.activate().status_code, status.HTTP_201_CREATED)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, UserSubscription.Status.ACTIVE)
        self.assertIsNone(subscription.canceled_at)

    # -- entitlement lifecycle ----------------------------------------------

    def test_an_expired_activation_falls_back_to_the_free_plan(self):
        self.activate()
        subscription = UserSubscription.objects.get(user=self.student)
        subscription.current_period_end = timezone.now() - timedelta(seconds=1)
        subscription.save(update_fields=['current_period_end'])

        self.assertEqual(get_user_plan(self.student).code, 'free')

    # -- idempotency and duplicate protection --------------------------------

    def test_repeating_the_same_request_changes_nothing(self):
        first = self.activate()
        end = UserSubscription.objects.get(user=self.student).current_period_end
        replay = self.activate()

        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertFalse(replay.data['created'])
        self.assertEqual(replay.data['activation']['id'], first.data['activation']['id'])
        self.assertEqual(UserSubscription.objects.get(user=self.student).current_period_end, end)
        self.assertEqual(SubscriptionActivation.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='subscription.manually_activated').count(), 1)

    def test_the_reference_matches_regardless_of_case_and_spacing(self):
        self.activate(payment_reference='TRX 1001')
        replay = self.activate(payment_reference='trx1001')

        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(SubscriptionActivation.objects.count(), 1)

    def test_a_reference_cannot_activate_a_second_student(self):
        self.activate()
        response = self.activate(user=self.other_student)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'payment_reference_already_used')
        self.assertEqual(SubscriptionActivation.objects.count(), 1)
        self.assertEqual(get_user_plan(self.other_student).code, 'free')

    def test_a_reference_cannot_buy_a_different_plan(self):
        self.activate()
        response = self.activate(plan_code='pro')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(UserSubscription.objects.get(user=self.student).plan.code, 'premium')

    # -- who may activate ----------------------------------------------------

    def test_only_roles_holding_the_permission_may_activate(self):
        for user in (self.basic_admin, self.support, self.analyst, self.student, self.other_student):
            response = self.activate(actor=user, payment_reference=f'NO-{user.pk}')
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, user.email)
        self.assertEqual(SubscriptionActivation.objects.count(), 0)

    def test_anonymous_callers_are_turned_away(self):
        self.client.force_authenticate(None)
        response = self.activate(actor=False)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.assertEqual(SubscriptionActivation.objects.count(), 0)

    def test_a_student_cannot_activate_their_own_plan(self):
        response = self.activate(user=self.student, actor=self.student)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(get_user_plan(self.student).code, 'free')

    def test_the_permission_is_seeded_for_finance_and_super_admin_only(self):
        permission = AdminPermission.objects.get(code='subscriptions.activate')
        holders = set(permission.roles.values_list('code', flat=True))
        self.assertEqual(holders, {'finance'} | ({'super_admin'} if 'super_admin' in holders else set()))
        self.assertNotIn('admin', holders)
        self.assertNotIn('support', holders)

    # -- validation ----------------------------------------------------------

    def test_a_payment_reference_is_required(self):
        response = self.activate(payment_reference='')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post(
            reverse('admin-managed-user-activate-subscription', args=[self.student.id]),
            {'plan_code': 'premium', 'payment_method': 'cash'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(SubscriptionActivation.objects.count(), 0)

    def test_a_card_number_is_refused_and_not_stored(self):
        response = self.activate(payment_reference='4111 1111 1111 1111')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(SubscriptionActivation.objects.count(), 0)
        self.assertNotIn('4111', str(AuditLog.objects.values_list('metadata', flat=True)))

    def test_an_unknown_payment_method_is_refused(self):
        self.assertEqual(self.activate(payment_method='crypto').status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_free_plan_cannot_be_activated(self):
        response = self.activate(plan_code='free')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'plan_not_paid')

    def test_an_inactive_or_unknown_plan_is_refused(self):
        SubscriptionPlan.objects.filter(code='pro').update(is_active=False)
        self.assertEqual(self.activate(plan_code='pro').status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.activate(plan_code='nope').status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.activate(plan_code=None).status_code, status.HTTP_400_BAD_REQUEST)

    def test_period_end_rules(self):
        SubscriptionPlan.objects.create(code='forever', name='F', price=Decimal('1'), billing_interval='lifetime')
        now = timezone.now()
        cases = [
            ({'current_period_end': (now - timedelta(days=1)).isoformat()}, 'period_end_in_past'),
            ({'current_period_end': (now + timedelta(days=366 * 6)).isoformat()}, 'period_end_too_far'),
            ({'plan_code': 'forever', 'current_period_end': (now + timedelta(days=5)).isoformat()}, 'period_end_not_allowed'),
            ({'plan_code': 'school'}, 'period_end_required'),
        ]
        for index, (overrides, code) in enumerate(cases):
            response = self.activate(payment_reference=f'PE-{index}', **overrides)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, (code, response.data))
            self.assertEqual(response.data['code'], code)
        self.assertEqual(SubscriptionActivation.objects.count(), 0)

    def test_a_period_end_may_not_shorten_what_the_student_already_has(self):
        self.activate(payment_reference='REF-S1')
        soon = (timezone.now() + timedelta(days=3)).isoformat()
        response = self.activate(payment_reference='REF-S2', current_period_end=soon)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'period_end_before_existing')

    def test_the_note_is_optional_and_stored(self):
        self.activate(note='  verified against the bank statement  ')
        self.assertEqual(SubscriptionActivation.objects.get().note, 'verified against the bank statement')

    # -- the ledger ----------------------------------------------------------

    def test_activations_are_immutable(self):
        self.activate()
        activation = SubscriptionActivation.objects.get()
        activation.note = 'edited'
        with self.assertRaises(ValueError):
            activation.save()
        with self.assertRaises(ValueError):
            activation.delete()

    def test_deleting_an_account_keeps_the_activation_record(self):
        from apps.users.services import delete_user_account

        self.activate()
        delete_user_account(self.student)

        activation = SubscriptionActivation.objects.get()
        # Soft-deleted and anonymised: the record survives and now points at an
        # account that holds no personal data.
        self.assertEqual(activation.user_id, self.student.pk)
        self.assertEqual(activation.payment_reference, 'TRX-1001')
        self.assertTrue(User.all_objects.get(pk=self.student.pk).email.endswith('@deleted.baraq.invalid'))

    def test_a_hard_delete_nulls_the_link_instead_of_blocking(self):
        self.activate()
        self.student.hard_delete()

        activation = SubscriptionActivation.objects.get()
        self.assertIsNone(activation.user_id)
        self.assertEqual(activation.payment_reference, 'TRX-1001')

    def test_the_ledger_is_listed_for_staff_who_may_view_subscriptions(self):
        self.activate()
        self.client.force_authenticate(self.finance)
        listing = self.client.get(reverse('admin-subscription-activation-list'))
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.data['count'], 1)
        self.assertEqual(listing.data['results'][0]['payment_reference'], 'TRX-1001')

        filtered = self.client.get(reverse('admin-subscription-activation-list'), {'user': self.other_student.pk})
        self.assertEqual(filtered.data['count'], 0)

        self.client.force_authenticate(self.student)
        self.assertEqual(
            self.client.get(reverse('admin-subscription-activation-list')).status_code, status.HTTP_403_FORBIDDEN
        )

    # -- atomicity -----------------------------------------------------------

    def test_a_failed_audit_write_undoes_the_whole_activation(self):
        plan = SubscriptionPlan.objects.get(code='premium')
        with (
            mock.patch('apps.admin_dashboard.services.AuditLog.objects.create', side_effect=RuntimeError('db down')),
            self.assertRaises(RuntimeError),
        ):
            activate_paid_subscription(
                self.student,
                plan,
                payment_method='cash',
                payment_reference='ATOMIC-1',
                actor=self.finance,
            )

        self.assertEqual(SubscriptionActivation.objects.count(), 0)
        self.assertEqual(get_user_plan(self.student).code, 'free')
        subscription = UserSubscription.objects.get(user=self.student)
        self.assertEqual(subscription.plan.code, 'free')
        self.assertNotIn('last_activation_id', subscription.metadata)
        # and the reference is still free to be used once the audit works again
        activation, created = activate_paid_subscription(
            self.student, plan, payment_method='cash', payment_reference='ATOMIC-1', actor=self.finance
        )
        self.assertTrue(created)

    # -- no other door ------------------------------------------------------

    def test_change_subscription_can_no_longer_grant_a_paid_plan(self):
        self.client.force_authenticate(self.finance)
        response = self.client.post(
            reverse('admin-managed-user-change-subscription', args=[self.student.id]),
            {'plan_code': 'premium'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'payment_activation_required')
        self.assertEqual(get_user_plan(self.student).code, 'free')

    def test_change_subscription_still_handles_free_downgrades_and_non_entitling_states(self):
        self.activate()
        self.client.force_authenticate(self.finance)
        url = reverse('admin-managed-user-change-subscription', args=[self.student.id])

        paused = self.client.post(url, {'plan_code': 'premium', 'status': 'paused'}, format='json')
        self.assertEqual(paused.status_code, status.HTTP_200_OK, paused.data)
        downgrade = self.client.post(url, {'plan_code': 'free'}, format='json')
        self.assertEqual(downgrade.status_code, status.HTTP_200_OK, downgrade.data)
        self.assertEqual(UserSubscription.objects.get(user=self.student).plan.code, 'free')

    def test_patching_a_subscription_cannot_grant_paid_access_either(self):
        subscription = get_or_create_user_subscription(self.student)
        self.client.force_authenticate(self.finance)
        url = reverse('admin-user-subscription-detail', args=[subscription.pk])

        for body in (
            {'plan_code': 'pro'},
            {'plan_code': 'pro', 'status': 'active'},
            {'plan_code': 'pro', 'status': 'trialing'},
        ):
            response = self.client.patch(url, body, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, body)
            self.assertEqual(response.data['code'], 'payment_activation_required')
        subscription.refresh_from_db()
        self.assertEqual(subscription.plan.code, 'free')

    def test_patching_cannot_revive_or_extend_a_paid_subscription(self):
        self.activate()
        subscription = UserSubscription.objects.get(user=self.student)
        url = reverse('admin-user-subscription-detail', args=[subscription.pk])
        self.client.force_authenticate(self.finance)

        extend = self.client.patch(
            url, {'current_period_end': (subscription.current_period_end + timedelta(days=30)).isoformat()}, format='json'
        )
        self.assertEqual(extend.status_code, status.HTTP_400_BAD_REQUEST)
        forever = self.client.patch(url, {'current_period_end': None}, format='json')
        self.assertEqual(forever.status_code, status.HTTP_400_BAD_REQUEST)

        cancel = self.client.patch(url, {'status': 'canceled'}, format='json')
        self.assertEqual(cancel.status_code, status.HTTP_200_OK, cancel.data)
        revive = self.client.patch(url, {'status': 'active'}, format='json')
        self.assertEqual(revive.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patching_harmless_fields_still_works(self):
        self.activate()
        subscription = UserSubscription.objects.get(user=self.student)
        self.client.force_authenticate(self.finance)
        response = self.client.patch(
            reverse('admin-user-subscription-detail', args=[subscription.pk]),
            {'metadata': {'support_note': 'called the student'}, 'auto_renew': False},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_cancelling_still_works(self):
        self.activate()
        self.client.force_authenticate(self.super_admin)
        response = self.client.post(
            reverse('admin-managed-user-cancel-subscription', args=[self.student.id]), format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(get_user_plan(self.student).code, 'free')

    def test_a_student_has_no_endpoint_that_writes_their_own_subscription(self):
        self.client.force_authenticate(self.student)
        mine = reverse('my-subscription')
        for method in ('post', 'put', 'patch', 'delete'):
            response = getattr(self.client, method)(mine, {'plan_code': 'pro', 'status': 'active'}, format='json')
            self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED, method)
        self.assertEqual(get_user_plan(self.student).code, 'free')


@override_settings(ALLOWED_HOSTS=HOSTS)
class ActivationConcurrencyTests(TransactionTestCase):
    """Two staff members pasting the same receipt at the same moment."""

    def setUp(self):
        _, roles = seed_default_rbac()
        ensure_default_plans()
        self.staff = User.objects.create_superuser(email='race-super@example.com', password='StrongPass123', full_name='R')
        self.students = [
            User.objects.create_user(email=f'race-{i}@example.com', password='StrongPass123', full_name='S')
            for i in range(2)
        ]
        self.plan = SubscriptionPlan.objects.get(code='premium')

    def _run_pair(self, targets):
        barrier = threading.Barrier(len(targets))
        outcomes = [None] * len(targets)

        def worker(index, user, plan):
            try:
                barrier.wait(timeout=10)
                _, created = activate_paid_subscription(
                    user, plan, payment_method='cash', payment_reference='RACE-1', actor=self.staff
                )
                outcomes[index] = 'created' if created else 'replayed'
            except SubscriptionError as exc:
                outcomes[index] = exc.domain_code
            except Exception as exc:  # noqa: BLE001 - reported through the assertion below
                outcomes[index] = repr(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i, u, p)) for i, (u, p) in enumerate(targets)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return outcomes

    def test_one_receipt_for_two_students_activates_exactly_one(self):
        outcomes = self._run_pair([(self.students[0], self.plan), (self.students[1], self.plan)])

        self.assertEqual(sorted(outcomes), ['created', 'payment_reference_already_used'])
        self.assertEqual(SubscriptionActivation.objects.count(), 1)
        paid = [s for s in self.students if get_user_plan(s).code == 'premium']
        self.assertEqual(len(paid), 1)

    def test_a_double_click_for_the_same_student_activates_once(self):
        outcomes = self._run_pair([(self.students[0], self.plan), (self.students[0], self.plan)])

        self.assertEqual(sorted(outcomes), ['created', 'replayed'])
        self.assertEqual(SubscriptionActivation.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='subscription.manually_activated').count(), 1)
