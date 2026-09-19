"""Tenant isolation.

The single most important property of this phase is not that organization
pages exist. It is that no account can read or mutate data outside the scope
the backend granted it -- including by calling the API directly with an id it
should never have seen.

So these tests attack the boundary rather than demonstrate the happy path:
direct ids, query parameters, request bodies, bulk-shaped actions and
aggregates, each checked for both managers in both directions.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.admin_dashboard.models import AdminPermission, AdminRole, AuditLog
from apps.admin_dashboard.services import assign_roles_to_user, seed_default_rbac
from apps.subscriptions.models import UserSubscription
from apps.support.models import SupportTicket

from .models import (
    JOIN_CODE_ALPHABET,
    JOIN_CODE_AMBIGUOUS,
    JOIN_CODE_LENGTH,
    AdminRoleScope,
    ClassMembership,
    Classroom,
    Invitation,
    JoinRequest,
    Organization,
    OrganizationMembership,
)
from .scope import TenantScopedQuerysetMixin
from .services import (
    OrganizationError,
    approve_join_request,
    reject_join_request,
    request_to_join,
    resolve_invitation,
)

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class TenantIsolationTestCase(APITestCase):
    """Two organizations, two of everything, and no path between them."""

    def setUp(self):
        cache.clear()
        _, self.roles = seed_default_rbac()

        self.super_admin = User.objects.create_user(
            email="root@example.com",
            password="StrongPass123",
            full_name="Root",
            role=User.Roles.SUPER_ADMIN,
            is_superuser=True,
        )

        self.org_a = Organization.objects.create(name="School A", created_by=self.super_admin)
        self.org_b = Organization.objects.create(
            name="Institute B",
            organization_type=Organization.Type.INSTITUTE,
            created_by=self.super_admin,
        )
        self.class_a = Classroom.objects.create(organization=self.org_a, name="10-A")
        self.class_b = Classroom.objects.create(organization=self.org_b, name="Networks")

        self.manager_a = self._scoped_admin("mgr-a@example.com", self.org_a)
        self.manager_b = self._scoped_admin("mgr-b@example.com", self.org_b)
        self.supervisor_a = self._class_admin("sup-a@example.com", self.class_a)

        self.student_a = self._student("student-a@example.com", self.org_a, self.class_a)
        self.student_b = self._student("student-b@example.com", self.org_b, self.class_b)

        self.invitation_a = Invitation.objects.create(
            organization=self.org_a, classroom=self.class_a, created_by=self.manager_a
        )
        self.invitation_b = Invitation.objects.create(
            organization=self.org_b, classroom=self.class_b, created_by=self.manager_b
        )

        self.request_a = JoinRequest.objects.create(
            invitation=self.invitation_a,
            user=self._plain_user("joiner-a@example.com"),
            organization=self.org_a,
            classroom=self.class_a,
        )
        self.request_b = JoinRequest.objects.create(
            invitation=self.invitation_b,
            user=self._plain_user("joiner-b@example.com"),
            organization=self.org_b,
            classroom=self.class_b,
        )

    # -- fixtures ---------------------------------------------------------
    def _plain_user(self, email):
        return User.objects.create_user(email=email, password="StrongPass123", full_name=email.split("@")[0])

    def _scoped_admin(self, email, organization):
        user = User.objects.create_user(email=email, password="StrongPass123", full_name=email, role=User.Roles.ADMIN)
        assign_roles_to_user(
            user,
            [self.roles["organization_manager"]],
            scopes=[
                {
                    "scope_type": AdminRoleScope.ScopeType.ORGANIZATION,
                    "organization": organization,
                }
            ],
        )
        return user

    def _class_admin(self, email, classroom):
        user = User.objects.create_user(email=email, password="StrongPass123", full_name=email, role=User.Roles.ADMIN)
        assign_roles_to_user(
            user,
            [self.roles["class_supervisor"]],
            scopes=[{"scope_type": AdminRoleScope.ScopeType.CLASS, "classroom": classroom}],
        )
        return user

    def _student(self, email, organization, classroom):
        user = self._plain_user(email)
        OrganizationMembership.objects.create(organization=organization, user=user, joined_at=timezone.now())
        ClassMembership.objects.create(classroom=classroom, user=user, joined_at=timezone.now())
        return user

    def _as(self, user):
        self.client.force_authenticate(user)

    # -- list isolation ---------------------------------------------------
    def test_manager_lists_only_their_own_organization(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("organization-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(names, {"School A"})

    def test_manager_lists_only_their_own_classes(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("classroom-list"))

        codes = {row["public_id"] for row in response.data["results"]}
        self.assertIn(str(self.class_a.public_id), codes)
        self.assertNotIn(str(self.class_b.public_id), codes)

    def test_each_manager_sees_only_their_own_side(self):
        """Both directions: a rule that only holds for A is not a rule."""
        for manager, mine, theirs in (
            (self.manager_a, self.class_a, self.class_b),
            (self.manager_b, self.class_b, self.class_a),
        ):
            with self.subTest(manager=manager.email):
                self._as(manager)
                ids = {row["public_id"] for row in self.client.get(reverse("classroom-list")).data["results"]}
                self.assertIn(str(mine.public_id), ids)
                self.assertNotIn(str(theirs.public_id), ids)

    # -- direct object access ---------------------------------------------
    def test_manager_cannot_retrieve_the_other_organization_by_id(self):
        """The classic leak: the list is scoped, the detail route is not."""
        self._as(self.manager_a)
        response = self.client.get(reverse("organization-detail", args=[str(self.org_b.public_id)]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_manager_cannot_retrieve_the_other_class_by_id(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("classroom-detail", args=[str(self.class_b.public_id)]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_manager_cannot_update_the_other_class(self):
        self._as(self.manager_a)
        response = self.client.patch(
            reverse("classroom-detail", args=[str(self.class_b.public_id)]),
            {"name": "renamed by intruder"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.class_b.refresh_from_db()
        self.assertEqual(self.class_b.name, "Networks")

    def test_manager_cannot_archive_the_other_class(self):
        self._as(self.manager_a)
        response = self.client.post(reverse("classroom-archive", args=[str(self.class_b.public_id)]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.class_b.refresh_from_db()
        self.assertEqual(self.class_b.status, Classroom.Status.ACTIVE)

    def test_manager_cannot_archive_the_other_organization(self):
        self._as(self.manager_a)
        response = self.client.post(reverse("organization-archive", args=[str(self.org_b.public_id)]))
        # Refused at the permission layer before scope is even consulted:
        # archiving an organization is a platform action, deliberately absent
        # from the organization_manager grant. Either refusal is correct; what
        # matters is that organization B is untouched.
        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )
        self.org_b.refresh_from_db()
        self.assertEqual(self.org_b.status, Organization.Status.ACTIVE)

    # -- body / query tampering -------------------------------------------
    def test_a_body_organization_id_cannot_place_a_class_in_another_tenant(self):
        """Naming a target is not the same as being allowed to reach it."""
        self._as(self.manager_a)
        response = self.client.post(
            reverse("classroom-list"),
            {"organization": str(self.org_b.public_id), "name": "smuggled"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Classroom.objects.filter(name="smuggled").exists())

    def test_a_query_parameter_cannot_widen_a_scoped_list(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("classroom-list"), {"organization": str(self.org_b.public_id)})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Scope is applied before the filter, so this can only ever return
        # fewer rows -- never other rows.
        self.assertEqual(response.data["results"], [])

    def test_a_transfer_target_in_another_tenant_is_refused(self):
        """Authorizing only the source would let a learner be moved into a
        class the caller cannot see."""
        membership = ClassMembership.objects.get(user=self.student_a)
        self._as(self.manager_a)
        response = self.client.post(
            reverse("classroom-transfer-member", args=[str(self.class_a.public_id)]),
            {"membership": membership.pk, "target_classroom": str(self.class_b.public_id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        membership.refresh_from_db()
        self.assertEqual(membership.classroom_id, self.class_a.id)

    # -- join requests ----------------------------------------------------
    def test_manager_cannot_see_the_other_tenants_join_requests(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("join-request-list"))

        ids = {row["public_id"] for row in response.data["results"]}
        self.assertIn(str(self.request_a.public_id), ids)
        self.assertNotIn(str(self.request_b.public_id), ids)

    def test_manager_cannot_approve_the_other_tenants_join_request(self):
        self._as(self.manager_a)
        response = self.client.post(reverse("join-request-approve", args=[str(self.request_b.public_id)]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.request_b.refresh_from_db()
        self.assertEqual(self.request_b.status, JoinRequest.Status.PENDING)
        self.assertFalse(ClassMembership.objects.filter(classroom=self.class_b, user=self.request_b.user).exists())

    # -- invitations ------------------------------------------------------
    def test_manager_cannot_revoke_the_other_tenants_invitation(self):
        self._as(self.manager_a)
        response = self.client.post(reverse("invitation-revoke", args=[self.invitation_b.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.invitation_b.refresh_from_db()
        self.assertEqual(self.invitation_b.status, Invitation.Status.ACTIVE)

    def test_invitation_list_is_scoped(self):
        self._as(self.manager_a)
        ids = {row["id"] for row in self.client.get(reverse("invitation-list")).data["results"]}
        self.assertIn(self.invitation_a.pk, ids)
        self.assertNotIn(self.invitation_b.pk, ids)

    # -- aggregates -------------------------------------------------------
    def test_overview_counts_only_the_managers_own_tenant(self):
        """A total leaks tenant shape as surely as a list does."""
        self._as(self.manager_a)
        response = self.client.get(reverse("organization-overview", args=[str(self.org_a.public_id)]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["active_classes"], 1)
        self.assertEqual(response.data["active_students"], 1)

    def test_overview_of_another_tenant_is_refused(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("organization-overview", args=[str(self.org_b.public_id)]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # -- class supervisor -------------------------------------------------
    def test_a_class_supervisor_reaches_exactly_one_class(self):
        self._as(self.supervisor_a)
        ids = {row["public_id"] for row in self.client.get(reverse("classroom-list")).data["results"]}
        self.assertEqual(ids, {str(self.class_a.public_id)})

    def test_a_class_supervisor_does_not_inherit_sibling_classes(self):
        """A grant on 10-A is not a grant on 10-B."""
        sibling = Classroom.objects.create(organization=self.org_a, name="10-B")
        self._as(self.supervisor_a)

        response = self.client.get(reverse("classroom-detail", args=[str(sibling.public_id)]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_class_supervisor_cannot_reach_another_tenant(self):
        self._as(self.supervisor_a)
        response = self.client.get(reverse("classroom-detail", args=[str(self.class_b.public_id)]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_class_supervisor_cannot_create_a_class(self):
        """class_supervisor holds no classes.create grant, so scope never
        even comes into it."""
        self._as(self.supervisor_a)
        response = self.client.post(
            reverse("classroom-list"),
            {"organization": str(self.org_a.public_id), "name": "unauthorised"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -- organization creation --------------------------------------------
    def test_a_scoped_manager_cannot_create_an_organization(self):
        """Otherwise a manager could conjure a second tenant and own it."""
        self._as(self.manager_a)
        response = self.client.post(reverse("organization-list"), {"name": "My own school"}, format="json")
        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )
        self.assertFalse(Organization.objects.filter(name="My own school").exists())

    # -- super admin regression -------------------------------------------
    def test_super_admin_still_reaches_every_tenant(self):
        self._as(self.super_admin)
        ids = {row["public_id"] for row in self.client.get(reverse("organization-list")).data["results"]}
        self.assertIn(str(self.org_a.public_id), ids)
        self.assertIn(str(self.org_b.public_id), ids)

    def test_super_admin_reaches_both_classes_directly(self):
        self._as(self.super_admin)
        for classroom in (self.class_a, self.class_b):
            with self.subTest(classroom=classroom.name):
                response = self.client.get(reverse("classroom-detail", args=[str(classroom.public_id)]))
                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_super_admin_can_create_an_organization(self):
        self._as(self.super_admin)
        response = self.client.post(
            reverse("organization-list"),
            {"name": "New School", "organization_type": "school"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    # -- students ---------------------------------------------------------
    def test_a_student_cannot_reach_the_admin_surface(self):
        self._as(self.student_a)
        for url in (reverse("organization-list"), reverse("classroom-list")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_reach_anything(self):
        for url in (reverse("organization-list"), reverse("my-memberships")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class JoinFlowTestCase(APITestCase):
    """Invitation preview, confirmation, approval -- and the ways each can be
    abused."""

    def setUp(self):
        cache.clear()
        _, self.roles = seed_default_rbac()
        self.super_admin = User.objects.create_user(
            email="root2@example.com",
            password="StrongPass123",
            full_name="Root",
            role=User.Roles.SUPER_ADMIN,
            is_superuser=True,
        )
        self.org = Organization.objects.create(name="School A", created_by=self.super_admin)
        self.classroom = Classroom.objects.create(organization=self.org, name="10-A")
        self.manager = User.objects.create_user(
            email="mgr@example.com",
            password="StrongPass123",
            full_name="Mgr",
            role=User.Roles.ADMIN,
        )
        assign_roles_to_user(
            self.manager,
            [self.roles["organization_manager"]],
            scopes=[
                {
                    "scope_type": AdminRoleScope.ScopeType.ORGANIZATION,
                    "organization": self.org,
                }
            ],
        )
        self.student = User.objects.create_user(
            email="learner@example.com", password="StrongPass123", full_name="Learner"
        )
        self.invitation = Invitation.objects.create(
            organization=self.org, classroom=self.classroom, created_by=self.manager
        )

    def _as(self, user):
        self.client.force_authenticate(user)

    # -- preview ----------------------------------------------------------
    def test_preview_shows_the_class_without_creating_anything(self):
        self._as(self.student)
        response = self.client.post(reverse("join-preview"), {"code": self.invitation.code}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["organization"]["name"], "School A")
        self.assertEqual(response.data["classroom"]["name"], "10-A")
        self.assertFalse(JoinRequest.objects.exists())
        self.assertFalse(ClassMembership.objects.exists())

    def test_preview_leaks_nothing_beyond_the_names(self):
        """A code that circulates further than intended must not become a
        directory of the school."""
        self._as(self.student)
        payload = self.client.post(reverse("join-preview"), {"code": self.invitation.code}, format="json").data

        flat = str(payload)
        for secret in (self.invitation.token, self.invitation.code, self.manager.email):
            self.assertNotIn(secret, flat)
        self.assertEqual(set(payload), {"organization", "classroom"})
        self.assertEqual(set(payload["organization"]), {"public_id", "name", "organization_type"})

    def test_a_bad_code_is_refused(self):
        self._as(self.student)
        response = self.client.post(reverse("join-preview"), {"code": "NOPENOPE"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "invitation_invalid")

    # -- confirm ----------------------------------------------------------
    def test_confirming_creates_a_pending_request_and_no_membership(self):
        self._as(self.student)
        response = self.client.post(reverse("join-confirm"), {"code": self.invitation.code}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], JoinRequest.Status.PENDING)
        self.assertFalse(ClassMembership.objects.filter(user=self.student).exists())

    def test_confirming_twice_does_not_queue_two_requests(self):
        """A learner tapping join twice must not give a manager two identical
        rows to work through."""
        self._as(self.student)
        first = self.client.post(reverse("join-confirm"), {"code": self.invitation.code}, format="json")
        second = self.client.post(reverse("join-confirm"), {"code": self.invitation.code}, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(JoinRequest.objects.filter(user=self.student).count(), 1)

    def test_a_revoked_invitation_cannot_be_used(self):
        self.invitation.status = Invitation.Status.REVOKED
        self.invitation.save(update_fields=["status"])
        self._as(self.student)

        response = self.client.post(reverse("join-confirm"), {"code": self.invitation.code}, format="json")

        self.assertEqual(response.data["code"], "invitation_revoked")
        self.assertFalse(JoinRequest.objects.exists())

    def test_an_expired_invitation_cannot_be_used(self):
        self.invitation.expires_at = timezone.now() - timedelta(minutes=1)
        self.invitation.save(update_fields=["expires_at"])
        self._as(self.student)

        response = self.client.post(reverse("join-confirm"), {"code": self.invitation.code}, format="json")

        self.assertEqual(response.data["code"], "invitation_expired")

    def test_an_exhausted_invitation_cannot_be_used(self):
        self.invitation.max_uses = 1
        self.invitation.usage_count = 1
        self.invitation.save(update_fields=["max_uses", "usage_count"])
        self._as(self.student)

        response = self.client.post(reverse("join-confirm"), {"code": self.invitation.code}, format="json")

        self.assertEqual(response.data["code"], "invitation_exhausted")

    # -- approval ---------------------------------------------------------
    def test_approval_creates_both_memberships(self):
        """A learner cannot be in 10-A without being at the school."""
        join_request, _ = request_to_join(user=self.student, invitation=self.invitation)

        approve_join_request(join_request=join_request, approved_by=self.manager)

        self.assertTrue(
            ClassMembership.objects.filter(classroom=self.classroom, user=self.student, status="active").exists()
        )
        self.assertTrue(
            OrganizationMembership.objects.filter(organization=self.org, user=self.student, status="active").exists()
        )

    def test_approving_twice_does_not_duplicate_membership(self):
        join_request, _ = request_to_join(user=self.student, invitation=self.invitation)

        approve_join_request(join_request=join_request, approved_by=self.manager)
        approve_join_request(join_request=join_request, approved_by=self.manager)

        self.assertEqual(ClassMembership.objects.filter(user=self.student, status="active").count(), 1)
        self.assertEqual(
            OrganizationMembership.objects.filter(user=self.student, status="active").count(),
            1,
        )

    def test_the_database_itself_refuses_a_second_active_membership(self):
        """Idempotence is not the guard; the constraint is.

        `approve_join_request` uses `get_or_create`, which covers the
        sequential double-click. It cannot cover two transactions that both
        miss the row and both insert -- exactly what two managers approving
        at the same instant produce. Only the partial unique constraint makes
        that impossible, so it is asserted directly against the database
        rather than through the service that hides it.
        """
        OrganizationMembership.objects.create(organization=self.org, user=self.student, joined_at=timezone.now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationMembership.objects.create(organization=self.org, user=self.student, joined_at=timezone.now())

    def test_the_database_itself_refuses_a_second_active_class_membership(self):
        ClassMembership.objects.create(classroom=self.classroom, user=self.student, joined_at=timezone.now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            ClassMembership.objects.create(classroom=self.classroom, user=self.student, joined_at=timezone.now())

    def test_a_removed_membership_does_not_block_rejoining(self):
        """The constraint is partial on purpose: a learner who left and came
        back must not be permanently locked out by their own history."""
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.student,
            status=OrganizationMembership.Status.REMOVED,
            removed_at=timezone.now(),
        )
        OrganizationMembership.objects.create(organization=self.org, user=self.student, joined_at=timezone.now())
        self.assertEqual(
            OrganizationMembership.objects.filter(organization=self.org, user=self.student, status="active").count(),
            1,
        )

    def test_rejecting_an_approved_request_does_not_remove_the_member(self):
        """Removing a learner is a different action with different
        authorization; rejection must not become a back door to it."""
        join_request, _ = request_to_join(user=self.student, invitation=self.invitation)
        approve_join_request(join_request=join_request, approved_by=self.manager)

        with self.assertRaises(OrganizationError) as caught:
            reject_join_request(join_request=join_request, rejected_by=self.manager)

        self.assertEqual(caught.exception.domain_code, "join_request_not_pending")
        self.assertTrue(ClassMembership.objects.filter(user=self.student, status="active").exists())

    def test_a_student_cannot_approve_their_own_request(self):
        join_request, _ = request_to_join(user=self.student, invitation=self.invitation)
        self._as(self.student)

        response = self.client.post(reverse("join-request-approve", args=[str(join_request.public_id)]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        join_request.refresh_from_db()
        self.assertEqual(join_request.status, JoinRequest.Status.PENDING)

    def test_the_manager_can_approve_through_the_api(self):
        join_request, _ = request_to_join(user=self.student, invitation=self.invitation)
        self._as(self.manager)

        response = self.client.post(reverse("join-request-approve", args=[str(join_request.public_id)]))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], JoinRequest.Status.APPROVED)

    def test_approval_consumes_one_invitation_use(self):
        self.invitation.max_uses = 2
        self.invitation.save(update_fields=["max_uses"])
        join_request, _ = request_to_join(user=self.student, invitation=self.invitation)

        approve_join_request(join_request=join_request, approved_by=self.manager)

        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.usage_count, 1)

    def test_an_already_active_member_is_told_rather_than_re_queued(self):
        join_request, _ = request_to_join(user=self.student, invitation=self.invitation)
        approve_join_request(join_request=join_request, approved_by=self.manager)

        with self.assertRaises(OrganizationError) as caught:
            request_to_join(user=self.student, invitation=self.invitation)

        self.assertEqual(caught.exception.domain_code, "membership_already_active")

    # -- learner self-service ---------------------------------------------
    def test_a_learner_sees_their_own_memberships_only(self):
        other = User.objects.create_user(email="other@example.com", password="StrongPass123", full_name="Other")
        ClassMembership.objects.create(classroom=self.classroom, user=other)
        join_request, _ = request_to_join(user=self.student, invitation=self.invitation)
        approve_join_request(join_request=join_request, approved_by=self.manager)

        self._as(self.student)
        response = self.client.get(reverse("my-memberships"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["classes"]), 1)
        self.assertNotIn("other@example.com", str(response.data))


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class DynamicRoleAndMultiScopeTestCase(APITestCase):
    """Role names are product labels. Permissions and scope are the authority.

    If a freshly invented role behaves exactly as its grants describe, with
    no code anywhere recognising its name, the architecture is genuinely
    dynamic rather than a fixed set of roles wearing a config file.
    """

    def setUp(self):
        cache.clear()
        _, self.roles = seed_default_rbac()
        self.super_admin = User.objects.create_user(
            email="root3@example.com",
            password="StrongPass123",
            full_name="Root",
            role=User.Roles.SUPER_ADMIN,
            is_superuser=True,
        )
        self.org_a = Organization.objects.create(name="School A")
        self.org_b = Organization.objects.create(name="School B")
        self.class_a = Classroom.objects.create(organization=self.org_a, name="10-A")
        self.class_b = Classroom.objects.create(organization=self.org_b, name="11-B")

    def _as(self, user):
        self.client.force_authenticate(user)

    def test_a_custom_role_behaves_exactly_as_its_permissions_say(self):
        from apps.admin_dashboard.models import AdminPermission

        coordinator = AdminRole.objects.create(code="academic_coordinator", name="Academic Coordinator")
        coordinator.permissions.set(
            AdminPermission.objects.filter(code__in=["dashboard.view", "classes.view", "join_requests.view"])
        )
        user = User.objects.create_user(
            email="coord@example.com",
            password="StrongPass123",
            full_name="Coord",
            role=User.Roles.ADMIN,
        )
        assign_roles_to_user(
            user,
            [coordinator],
            scopes=[
                {
                    "scope_type": AdminRoleScope.ScopeType.ORGANIZATION,
                    "organization": self.org_a,
                }
            ],
        )
        self._as(user)

        # Granted: sees its own organization's classes.
        listed = self.client.get(reverse("classroom-list"))
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {row["public_id"] for row in listed.data["results"]},
            {str(self.class_a.public_id)},
        )
        # Not granted: creating a class, despite an organization scope.
        created = self.client.post(
            reverse("classroom-list"),
            {"organization": str(self.org_a.public_id), "name": "nope"},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_403_FORBIDDEN)
        # Never granted: the other tenant.
        self.assertEqual(
            self.client.get(reverse("classroom-detail", args=[str(self.class_b.public_id)])).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_one_account_can_hold_two_scopes_without_merging_them(self):
        """Organization A plus class B in another organization -- the union of
        the grants, and emphatically not organization B."""
        user = User.objects.create_user(
            email="dual@example.com",
            password="StrongPass123",
            full_name="Dual",
            role=User.Roles.ADMIN,
        )
        assign_roles_to_user(
            user,
            [self.roles["organization_manager"]],
            scopes=[
                {
                    "scope_type": AdminRoleScope.ScopeType.ORGANIZATION,
                    "organization": self.org_a,
                }
            ],
        )
        assign_roles_to_user(
            user,
            [self.roles["organization_manager"], self.roles["class_supervisor"]],
            scopes=[
                {
                    "scope_type": AdminRoleScope.ScopeType.ORGANIZATION,
                    "organization": self.org_a,
                },
            ],
        )
        supervisor_assignment = user.admin_user_roles.get(role=self.roles["class_supervisor"])
        AdminRoleScope.objects.filter(admin_user_role=supervisor_assignment).delete()
        AdminRoleScope.objects.create(
            admin_user_role=supervisor_assignment,
            scope_type=AdminRoleScope.ScopeType.CLASS,
            classroom=self.class_b,
        )
        sibling_in_b = Classroom.objects.create(organization=self.org_b, name="11-C")
        self._as(user)

        ids = {row["public_id"] for row in self.client.get(reverse("classroom-list")).data["results"]}

        self.assertIn(str(self.class_a.public_id), ids, "lost the organization grant")
        self.assertIn(str(self.class_b.public_id), ids, "lost the class grant")
        # The class grant in organization B must not become organization B.
        self.assertNotIn(str(sibling_in_b.public_id), ids)

    def test_an_assignment_without_a_scope_grants_nothing(self):
        """Fail closed: a forgotten scope must not default to global."""
        user = User.objects.create_user(
            email="unscoped@example.com",
            password="StrongPass123",
            full_name="Unscoped",
            role=User.Roles.ADMIN,
        )
        assign_roles_to_user(user, [self.roles["organization_manager"]])
        AdminRoleScope.objects.filter(admin_user_role__user=user).delete()
        self._as(user)

        # Every scoped list, not just one: an empty scope that fails closed
        # for classes but open for organizations is still a leak, and a
        # single-route assertion would not notice.
        for route in ("classroom-list", "organization-list", "invitation-list", "join-request-list"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["results"], [])

    def test_the_default_assignment_stays_global_for_existing_callers(self):
        """Every caller before this phase meant "global". Changing that
        silently would have quietly demoted every existing admin."""
        user = User.objects.create_user(
            email="legacy@example.com",
            password="StrongPass123",
            full_name="Legacy",
            role=User.Roles.ADMIN,
        )
        assign_roles_to_user(user, [self.roles["organization_manager"]])

        scopes = AdminRoleScope.objects.filter(admin_user_role__user=user)
        self.assertEqual(scopes.count(), 1)
        self.assertEqual(scopes.first().scope_type, AdminRoleScope.ScopeType.GLOBAL)


class InvitationSecurityTestCase(APITestCase):
    """The credential itself."""

    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name="School A")
        self.classroom = Classroom.objects.create(organization=self.org, name="10-A")

    def test_tokens_and_codes_are_unpredictable_and_unique(self):
        invitations = [Invitation.objects.create(organization=self.org, classroom=self.classroom) for _ in range(25)]

        self.assertEqual(len({item.token for item in invitations}), 25)
        self.assertEqual(len({item.code for item in invitations}), 25)
        for item in invitations:
            self.assertGreaterEqual(len(item.token), 32)
            self.assertEqual(len(item.code), JOIN_CODE_LENGTH)
        # Never derived from a sequential id: an enumerable identifier used as
        # a credential is not a credential. Rows created in ascending pk order
        # must not yield codes in ascending order.
        codes_in_pk_order = [item.code for item in sorted(invitations, key=lambda i: i.pk)]
        self.assertNotEqual(codes_in_pk_order, sorted(codes_in_pk_order))

    def test_join_codes_avoid_characters_learners_mistype(self):
        """A code that is hard to read off a whiteboard gets retried, and
        retries against a secret look exactly like brute force."""
        codes = "".join(
            Invitation.objects.create(organization=self.org, classroom=self.classroom).code for _ in range(40)
        )
        for ambiguous in JOIN_CODE_AMBIGUOUS:
            self.assertNotIn(ambiguous, codes, f"{ambiguous} is mistaken for another character")
        self.assertTrue(set(codes) <= set(JOIN_CODE_ALPHABET))

    def test_resolving_an_unknown_code_raises_the_generic_code(self):
        with self.assertRaises(OrganizationError) as caught:
            resolve_invitation(code="ZZZZZZZZ")
        self.assertEqual(caught.exception.domain_code, "invitation_invalid")

    def test_an_archived_organization_stops_accepting_members(self):
        from .services import archive_organization

        archive_organization(organization=self.org)
        invitation = Invitation.objects.create(organization=self.org, classroom=self.classroom)

        with self.assertRaises(OrganizationError) as caught:
            resolve_invitation(code=invitation.code)

        self.assertEqual(caught.exception.domain_code, "organization_unavailable")

    def test_archiving_an_organization_preserves_its_history(self):
        from .services import archive_organization

        user = User.objects.create_user(email="hist@example.com", password="StrongPass123", full_name="Hist")
        membership = OrganizationMembership.objects.create(organization=self.org, user=user, joined_at=timezone.now())

        archive_organization(organization=self.org)

        membership.refresh_from_db()
        self.assertEqual(membership.status, OrganizationMembership.Status.ACTIVE)
        self.assertTrue(Classroom.objects.filter(pk=self.classroom.pk).exists())


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class AdminSurfaceScopingTestCase(APITestCase):
    """The surfaces that existed before organizations did.

    Sources, plans, quizzes, tickets, subscriptions, AI results and audit
    entries carry no organization column -- they are tenant data by virtue
    of whose they are. Scoping the new organization endpoints while leaving
    these global would have been theatre: a manager who cannot list another
    school's classes but can list its learners' support tickets is not
    isolated.
    """

    def setUp(self):
        cache.clear()
        _, self.roles = seed_default_rbac()
        self.super_admin = User.objects.create_user(
            email="root3@example.com",
            password="StrongPass123",
            full_name="Root",
            role=User.Roles.SUPER_ADMIN,
            is_superuser=True,
        )
        self.org_a = Organization.objects.create(name="School A")
        self.org_b = Organization.objects.create(name="School B")
        self.class_a = Classroom.objects.create(organization=self.org_a, name="10-A")
        self.class_b = Classroom.objects.create(organization=self.org_b, name="10-B")

        self.learner_a = self._member("learner-a@example.com", self.org_a, self.class_a)
        self.learner_b = self._member("learner-b@example.com", self.org_b, self.class_b)

        # A role holding every read permission, so what follows can only be
        # explained by scope. Proving this with a role that lacks the
        # permission would say nothing about the tenant boundary.
        self.wide_role = AdminRole.objects.create(code="wide_reader", name="Wide reader")
        self.wide_role.permissions.set(
            AdminPermission.objects.filter(
                code__in=[
                    "dashboard.view",
                    "users.view",
                    "admins.view",
                    "sources.view",
                    "study_plans.view",
                    "quizzes.view",
                    "analytics.view",
                    "support.view",
                    "audit_logs.view",
                    "subscriptions.view",
                    "roles.view",
                    "subjects.view",
                    "ai_jobs.view",
                ]
            )
        )
        self.manager_a = self._scoped("mgr-wide-a@example.com", self.org_a)
        self.manager_b = self._scoped("mgr-wide-b@example.com", self.org_b)

        self.ticket_a = SupportTicket.objects.create(user=self.learner_a, subject="A cannot log in")
        self.ticket_b = SupportTicket.objects.create(user=self.learner_b, subject="B cannot log in")
        # Every account already has exactly one subscription, created with it.
        self.sub_a = UserSubscription.objects.get(user=self.learner_a)
        self.sub_b = UserSubscription.objects.get(user=self.learner_b)
        AuditLog.objects.create(actor=self.learner_a, action="a.did.something")
        AuditLog.objects.create(actor=self.learner_b, action="b.did.something")

    def _member(self, email, organization, classroom):
        user = User.objects.create_user(email=email, password="StrongPass123", full_name=email.split("@")[0])
        OrganizationMembership.objects.create(organization=organization, user=user, joined_at=timezone.now())
        ClassMembership.objects.create(classroom=classroom, user=user, joined_at=timezone.now())
        return user

    def _scoped(self, email, organization):
        user = User.objects.create_user(email=email, password="StrongPass123", full_name=email, role=User.Roles.ADMIN)
        assign_roles_to_user(
            user,
            [self.wide_role],
            scopes=[
                {
                    "scope_type": AdminRoleScope.ScopeType.ORGANIZATION,
                    "organization": organization,
                }
            ],
        )
        return user

    def _as(self, user):
        self.client.force_authenticate(user)

    # -- learner-owned surfaces -------------------------------------------
    def test_the_learner_directory_is_scoped(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("admin-managed-user-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        emails = {row["email"] for row in response.data["results"]}
        self.assertIn(self.learner_a.email, emails)
        self.assertNotIn(self.learner_b.email, emails)

    def test_support_tickets_are_scoped_in_both_directions(self):
        for manager, mine, theirs in (
            (self.manager_a, self.ticket_a, self.ticket_b),
            (self.manager_b, self.ticket_b, self.ticket_a),
        ):
            with self.subTest(manager=manager.email):
                self._as(manager)
                response = self.client.get(reverse("admin-support-ticket-list"))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                ids = {row["id"] for row in response.data["results"]}
                self.assertIn(mine.id, ids)
                self.assertNotIn(theirs.id, ids)

    def test_a_ticket_in_another_tenant_is_not_reachable_by_direct_id(self):
        """The detail route, not the list: scoping one and not the other is
        the most common way this boundary is actually crossed."""
        self._as(self.manager_a)
        response = self.client.get(reverse("admin-support-ticket-detail", args=[self.ticket_b.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_subscriptions_are_scoped(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("admin-user-subscription-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(self.sub_a.id, ids)
        self.assertNotIn(self.sub_b.id, ids)

    def test_audit_entries_are_scoped_to_their_actor(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("admin-audit-log-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        actions = {row["action"] for row in response.data["results"]}
        self.assertIn("a.did.something", actions)
        self.assertNotIn("b.did.something", actions)

    # -- aggregates --------------------------------------------------------
    def test_platform_totals_are_refused_to_a_scoped_account(self):
        """A count describes the shape of every tenant it covers."""
        self._as(self.manager_a)
        response = self.client.get(reverse("admin-overview"))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["code"], "global_scope_required")

    def test_platform_ai_usage_is_refused_to_a_scoped_account(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("admin-ai-usage"))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_the_super_admin_still_sees_platform_totals(self):
        self._as(self.super_admin)
        response = self.client.get(reverse("admin-overview"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data["users_count"], 5)

    # -- what scope must NOT hide -----------------------------------------
    def test_the_super_admin_still_sees_every_tenant(self):
        self._as(self.super_admin)
        emails = {row["email"] for row in self.client.get(reverse("admin-managed-user-list")).data["results"]}
        self.assertIn(self.learner_a.email, emails)
        self.assertIn(self.learner_b.email, emails)

    def test_platform_catalogues_stay_visible_to_a_scoped_account(self):
        """Scope answers "whose data", and a role definition is nobody's.

        Hiding the catalogue would break the dashboard without protecting
        anything -- there is no tenant inside a permission code.
        """
        self._as(self.manager_a)
        response = self.client.get(reverse("admin-role-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(response.data["results"]), 0)

    def test_a_manager_sees_their_own_account_but_not_the_other_tenants_staff(self):
        self._as(self.manager_a)
        response = self.client.get(reverse("admin-user-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        emails = {row["email"] for row in response.data["results"]}
        self.assertIn(self.manager_a.email, emails)
        self.assertNotIn(self.manager_b.email, emails)
        self.assertNotIn(self.super_admin.email, emails)

    # -- the structural guarantee ------------------------------------------
    def test_every_admin_viewset_declares_its_tenancy(self):
        """The rule that keeps this true after today.

        The next admin endpoint will be added by someone who is not thinking
        about tenancy. Walking the URL conf and refusing an undeclared
        viewset turns "remember to scope it" into something the suite
        enforces, which is the only version of that instruction that
        survives contact with a deadline.
        """
        unset = TenantScopedQuerysetMixin._UNSET
        undeclared = []
        for cls in _admin_view_classes():
            if not issubclass(cls, TenantScopedQuerysetMixin):
                undeclared.append(cls.__name__ + " (not scoped at all)")
            elif getattr(cls, "tenant_user_field", unset) == unset:
                undeclared.append(cls.__name__ + " (no tenant_user_field)")
        self.assertEqual(undeclared, [], "admin viewsets missing a tenancy declaration")


def _admin_view_classes():
    """Every admin viewset reachable through the URL conf."""
    from django.urls import get_resolver

    from apps.admin_dashboard.permissions import IsAdminDashboardUser

    seen = {}

    def walk(resolver):
        for entry in resolver.url_patterns:
            if hasattr(entry, "url_patterns"):
                walk(entry)
                continue
            cls = getattr(entry.callback, "cls", None) or getattr(entry.callback, "view_class", None)
            if cls is None:
                continue
            if not hasattr(cls, "queryset") and not hasattr(cls, "get_queryset"):
                continue
            if IsAdminDashboardUser in tuple(getattr(cls, "permission_classes", ())):
                seen[cls.__name__] = cls

    walk(get_resolver())
    return list(seen.values())


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class ScopeGrantTestCase(APITestCase):
    """Granting a role must not be a way to invent reach.

    `admins.assign_roles` is the most dangerous permission in the system
    once scope exists: if an operator can attach any scope to any role, a
    manager of one school can hand themselves -- or a colleague -- the whole
    platform, and every other test in this file becomes decorative.
    """

    def setUp(self):
        cache.clear()
        _, self.roles = seed_default_rbac()
        self.super_admin = User.objects.create_user(
            email="root4@example.com",
            password="StrongPass123",
            full_name="Root",
            role=User.Roles.SUPER_ADMIN,
            is_superuser=True,
        )
        self.org_a = Organization.objects.create(name="School A")
        self.org_b = Organization.objects.create(name="School B")
        self.class_b = Classroom.objects.create(organization=self.org_b, name="10-B")

        # The granter holds everything organization_manager holds, plus the
        # admin permissions -- otherwise the pre-existing "cannot assign
        # permissions you do not have" check would refuse first and these
        # tests would pass without ever reaching the scope rule.
        self.granter_role = AdminRole.objects.create(code="granter", name="Granter")
        manager_permissions = list(self.roles["organization_manager"].permissions.values_list("code", flat=True))
        self.granter_role.permissions.set(
            AdminPermission.objects.filter(
                code__in=manager_permissions + ["admins.view", "admins.create", "admins.assign_roles"]
            )
        )
        self.manager_a = User.objects.create_user(
            email="granter-a@example.com",
            password="StrongPass123",
            full_name="Granter A",
            role=User.Roles.ADMIN,
        )
        assign_roles_to_user(
            self.manager_a,
            [self.granter_role],
            scopes=[
                {
                    "scope_type": AdminRoleScope.ScopeType.ORGANIZATION,
                    "organization": self.org_a,
                }
            ],
        )
        # Already inside organization A, so the admin directory shows them to
        # manager_a at all. An admin with no overlapping scope is invisible,
        # which is correct but would refuse these requests for the wrong
        # reason -- a 404 for "not your admin" rather than a 400 for "not
        # your scope to grant".
        self.colleague = User.objects.create_user(
            email="colleague@example.com",
            password="StrongPass123",
            full_name="Colleague",
            role=User.Roles.ADMIN,
        )
        assign_roles_to_user(
            self.colleague,
            [self.roles["class_supervisor"]],
            scopes=[
                {
                    "scope_type": AdminRoleScope.ScopeType.ORGANIZATION,
                    "organization": self.org_a,
                }
            ],
        )

    def _as(self, user):
        self.client.force_authenticate(user)

    def _assign(self, target, payload):
        return self.client.post(reverse("admin-user-assign-roles", args=[target.id]), payload, format="json")

    def _scopes_of(self, user):
        # Only active assignments: re-assigning a role deactivates the old
        # AdminUserRole rather than deleting it, and their scope rows stay
        # behind as history. The policy reads active rows only, so a test
        # that counted the rest would disagree with the thing it is testing.
        return set(
            AdminRoleScope.objects.filter(admin_user_role__user=user, admin_user_role__is_active=True).values_list(
                "scope_type", "organization_id", "classroom_id"
            )
        )

    def _unchanged(self, user, before):
        self.assertEqual(self._scopes_of(user), before)

    # -- escalation --------------------------------------------------------
    def test_a_scoped_operator_cannot_grant_platform_wide_scope(self):
        before = self._scopes_of(self.colleague)
        self._as(self.manager_a)
        response = self._assign(
            self.colleague,
            {"role_codes": ["organization_manager"], "scopes": [{"scope_type": "global"}]},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self._unchanged(self.colleague, before)

    def test_a_scoped_operator_cannot_grant_another_tenants_organization(self):
        before = self._scopes_of(self.colleague)
        self._as(self.manager_a)
        response = self._assign(
            self.colleague,
            {
                "role_codes": ["organization_manager"],
                "scopes": [{"scope_type": "organization", "organization": str(self.org_b.public_id)}],
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self._unchanged(self.colleague, before)

    def test_a_scoped_operator_cannot_grant_another_tenants_class(self):
        before = self._scopes_of(self.colleague)
        self._as(self.manager_a)
        response = self._assign(
            self.colleague,
            {
                "role_codes": ["class_supervisor"],
                "scopes": [{"scope_type": "class", "classroom": str(self.class_b.public_id)}],
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self._unchanged(self.colleague, before)

    def test_omitting_the_scope_no_longer_silently_means_global(self):
        """The default that would have undone everything.

        Before scopes were accepted here, every assignment made through the
        dashboard was global -- so the first organization manager created
        through the UI would have reached every school on the platform.
        """
        before = self._scopes_of(self.colleague)
        self._as(self.manager_a)
        response = self._assign(self.colleague, {"role_codes": ["organization_manager"]})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self._unchanged(self.colleague, before)

    # -- what must still work ---------------------------------------------
    def test_a_scoped_operator_can_grant_their_own_organization(self):
        self._as(self.manager_a)
        response = self._assign(
            self.colleague,
            {
                "role_codes": ["organization_manager"],
                "scopes": [{"scope_type": "organization", "organization": str(self.org_a.public_id)}],
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        granted = AdminRoleScope.objects.get(admin_user_role__user=self.colleague, admin_user_role__is_active=True)
        self.assertEqual(granted.scope_type, AdminRoleScope.ScopeType.ORGANIZATION)
        self.assertEqual(granted.organization_id, self.org_a.id)

    def test_the_super_admin_can_still_grant_anything(self):
        self._as(self.super_admin)
        response = self._assign(
            self.colleague,
            {
                "role_codes": ["organization_manager"],
                "scopes": [{"scope_type": "organization", "organization": str(self.org_b.public_id)}],
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            AdminRoleScope.objects.get(
                admin_user_role__user=self.colleague, admin_user_role__is_active=True
            ).organization_id,
            self.org_b.id,
        )

    def test_the_super_admin_still_gets_global_by_omission(self):
        """Existing callers keep working: an omitted scope from a platform
        admin still means what it always meant."""
        self._as(self.super_admin)
        response = self._assign(self.colleague, {"role_codes": ["organization_manager"]})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {row[0] for row in self._scopes_of(self.colleague)},
            {AdminRoleScope.ScopeType.GLOBAL},
        )

    def test_creating_an_admin_carries_the_same_rule(self):
        """The other door into the same room.

        Roles can also be attached while creating an account, and that path
        defaulted to global too -- fixing only assign_roles would have left
        the escalation one endpoint away.
        """
        self._as(self.manager_a)
        response = self.client.post(
            reverse("admin-user-list"),
            {
                "email": "new-admin@example.com",
                "full_name": "New Admin",
                "password": "StrongPass123",
                "role_codes": ["organization_manager"],
                "scopes": [{"scope_type": "organization", "organization": str(self.org_b.public_id)}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email="new-admin@example.com").exists())
