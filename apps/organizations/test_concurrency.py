"""Concurrency invariants, proven with real parallel transactions.

SQLite cannot prove any of this. It serialises writers, and
`select_for_update()` is a documented no-op there, so every race in this
file passes locally for the wrong reason. These tests therefore skip unless
the database is PostgreSQL, and are the gate that closes when a real stack
runs them -- in CI, in staging, or anywhere `DATABASE_URL` points at
Postgres.

They are deliberately written against threads and real connections rather
than mocked locks. A lock that is only asserted in a unit test is a comment
with a green tick next to it.

Each case follows the same shape: run the same operation from N connections
at once, then assert the *database* ended in one legal state. The
application-level `get_or_create` and status checks are expected to win most
races on their own; what these prove is that when they lose, the constraint
underneath still holds.
"""

from __future__ import annotations

import threading
import unittest

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.admin_dashboard.services import assign_roles_to_user, seed_default_rbac

from .models import (
    AdminRoleScope,
    ClassMembership,
    Classroom,
    Invitation,
    JoinRequest,
    Organization,
    OrganizationMembership,
)
from .services import OrganizationError, approve_join_request

User = get_user_model()

POSTGRES_ONLY = unittest.skipUnless(
    connection.vendor == "postgresql",
    "Concurrency semantics require PostgreSQL: SQLite serialises writers and "
    "select_for_update() is a no-op, so these races cannot fail there.",
)


def run_concurrently(target, times):
    """Run `target` on `times` threads and collect what each one raised.

    Every thread closes its own connection afterwards. Django hands each
    thread a fresh connection and will otherwise leave them open, which
    exhausts the pool long before a suite finishes.
    """
    barrier = threading.Barrier(times)
    results: list[BaseException | None] = [None] * times

    def worker(index):
        try:
            # Start together, so the operations genuinely overlap rather
            # than queueing behind each other's setup.
            barrier.wait(timeout=10)
            target(index)
        except BaseException as exc:  # noqa: BLE001 - recorded, then asserted on
            results[index] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(times)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    return results


@POSTGRES_ONLY
class MembershipConcurrencyTests(TransactionTestCase):
    """Two managers, one learner, the same instant."""

    def setUp(self):
        self.organization = Organization.objects.create(name="Race School")
        self.classroom = Classroom.objects.create(organization=self.organization, name="10-A")
        self.manager = User.objects.create_user(
            email="race-mgr@example.com", password="StrongPass123", full_name="Mgr"
        )
        self.learner = User.objects.create_user(
            email="race-learner@example.com", password="StrongPass123", full_name="Learner"
        )
        self.invitation = Invitation.objects.create(
            organization=self.organization, classroom=self.classroom
        )
        self.join_request = JoinRequest.objects.create(
            invitation=self.invitation,
            user=self.learner,
            organization=self.organization,
            classroom=self.classroom,
        )

    def test_simultaneous_approval_creates_one_membership(self):
        """The row lock is what makes double-approval safe, not the check.

        `approve_join_request` reads the request, sees PENDING, and writes a
        membership. Without `select_for_update`, two managers both read
        PENDING and both write.
        """

        def approve(_index):
            with transaction.atomic():
                approve_join_request(join_request=self.join_request, approved_by=self.manager)

        run_concurrently(approve, times=4)

        self.assertEqual(
            OrganizationMembership.objects.filter(
                organization=self.organization, user=self.learner, status="active"
            ).count(),
            1,
        )
        self.assertEqual(
            ClassMembership.objects.filter(
                classroom=self.classroom, user=self.learner, status="active"
            ).count(),
            1,
        )
        self.join_request.refresh_from_db()
        self.assertEqual(self.join_request.status, JoinRequest.Status.APPROVED)

    def test_the_constraint_stops_a_duplicate_the_service_never_saw(self):
        """Bypassing the service entirely, as a second process would.

        `get_or_create` cannot help when two transactions both miss the row
        and both insert. Exactly one insert must survive; the rest must be
        rejected by the partial unique index rather than by Python.
        """

        def insert(_index):
            with transaction.atomic():
                OrganizationMembership.objects.create(
                    organization=self.organization,
                    user=self.learner,
                    joined_at=timezone.now(),
                )

        results = run_concurrently(insert, times=5)

        survived = OrganizationMembership.objects.filter(
            organization=self.organization, user=self.learner, status="active"
        ).count()
        rejected = [r for r in results if isinstance(r, IntegrityError)]

        self.assertEqual(survived, 1)
        self.assertEqual(len(rejected), 4, "the database, not the application, must be the backstop")


@POSTGRES_ONLY
class InvitationConcurrencyTests(TransactionTestCase):
    """`max_uses` means nothing without a lock."""

    def setUp(self):
        self.organization = Organization.objects.create(name="Seat School")
        self.classroom = Classroom.objects.create(organization=self.organization, name="10-A")
        self.manager = User.objects.create_user(
            email="seat-mgr@example.com", password="StrongPass123", full_name="Mgr"
        )
        # Three seats, and five learners reaching for them together.
        self.invitation = Invitation.objects.create(
            organization=self.organization, classroom=self.classroom, max_uses=3
        )
        self.learners = [
            User.objects.create_user(
                email=f"seat-{i}@example.com", password="StrongPass123", full_name=f"L{i}"
            )
            for i in range(5)
        ]
        self.requests = [
            JoinRequest.objects.create(
                invitation=self.invitation,
                user=learner,
                organization=self.organization,
                classroom=self.classroom,
            )
            for learner in self.learners
        ]

    def test_usage_count_matches_the_approvals_that_happened(self):
        """`count += 1` over unlocked data loses increments.

        Five concurrent approvals against three seats must approve exactly
        three and leave usage_count at exactly three -- not fewer ("whatever
        the last writer saw plus one"), and not more: approval rechecks the
        seats under the lock, so the two late requests are refused as
        exhausted instead of overbooking the class.
        """

        def approve(index):
            with transaction.atomic():
                approve_join_request(join_request=self.requests[index], approved_by=self.manager)

        results = run_concurrently(approve, times=5)

        self.invitation.refresh_from_db()
        approved = JoinRequest.objects.filter(
            invitation=self.invitation, status=JoinRequest.Status.APPROVED
        ).count()

        refused = [result for result in results if result is not None]
        self.assertEqual(approved, 3)
        self.assertEqual(len(refused), 2, refused)
        for error in refused:
            self.assertIsInstance(error, OrganizationError)
            self.assertEqual(error.domain_code, "invitation_exhausted")
        self.assertEqual(
            self.invitation.usage_count,
            approved,
            "lost increments: the counter was read outside the lock",
        )


@POSTGRES_ONLY
class ScopeAssignmentConcurrencyTests(TransactionTestCase):
    """Re-assigning a role from two places at once."""

    def setUp(self):
        _, self.roles = seed_default_rbac()
        self.organization = Organization.objects.create(name="Scope School")
        self.other = Organization.objects.create(name="Other School")
        self.target = User.objects.create_user(
            email="scope-target@example.com", password="StrongPass123", full_name="T"
        )

    def test_concurrent_assignment_leaves_one_coherent_scope_set(self):
        """Two operators assigning at once must not interleave into a union.

        `assign_roles_to_user` replaces scope rows. If two calls interleave,
        the account can end up holding both organizations -- reach nobody
        granted. The end state must be one operator's intent, not a merge.
        """
        role = self.roles["organization_manager"]
        targets = [self.organization, self.other]

        def assign(index):
            with transaction.atomic():
                assign_roles_to_user(
                    self.target,
                    [role],
                    scopes=[
                        {
                            "scope_type": AdminRoleScope.ScopeType.ORGANIZATION,
                            "organization": targets[index % 2],
                        }
                    ],
                )

        run_concurrently(assign, times=6)

        rows = AdminRoleScope.objects.filter(
            admin_user_role__user=self.target, admin_user_role__is_active=True
        )
        organizations = {row.organization_id for row in rows}

        self.assertEqual(
            len(organizations),
            1,
            f"interleaved assignment produced reach over {len(organizations)} organizations",
        )
        self.assertLessEqual(rows.count(), 1)


@POSTGRES_ONLY
class ClassMembershipConcurrencyTests(TransactionTestCase):
    """The class-side equivalent of the membership constraint."""

    def setUp(self):
        self.organization = Organization.objects.create(name="Class Race School")
        self.classroom = Classroom.objects.create(organization=self.organization, name="10-A")
        self.learner = User.objects.create_user(
            email="class-race@example.com", password="StrongPass123", full_name="L"
        )

    def test_only_one_active_class_membership_survives(self):
        def insert(_index):
            with transaction.atomic():
                ClassMembership.objects.create(
                    classroom=self.classroom, user=self.learner, joined_at=timezone.now()
                )

        results = run_concurrently(insert, times=4)

        self.assertEqual(
            ClassMembership.objects.filter(
                classroom=self.classroom, user=self.learner, status="active"
            ).count(),
            1,
        )
        self.assertEqual(len([r for r in results if isinstance(r, IntegrityError)]), 3)

    def test_a_removed_membership_still_allows_rejoining_under_contention(self):
        """The constraint is partial on purpose, and stays partial in a race."""
        ClassMembership.objects.create(
            classroom=self.classroom,
            user=self.learner,
            status=ClassMembership.Status.REMOVED,
            removed_at=timezone.now(),
        )

        def insert(_index):
            with transaction.atomic():
                ClassMembership.objects.create(
                    classroom=self.classroom, user=self.learner, joined_at=timezone.now()
                )

        run_concurrently(insert, times=3)

        self.assertEqual(
            ClassMembership.objects.filter(
                classroom=self.classroom, user=self.learner, status="active"
            ).count(),
            1,
        )
        self.assertEqual(
            ClassMembership.objects.filter(
                classroom=self.classroom, user=self.learner, status="removed"
            ).count(),
            1,
            "history was destroyed by the rejoin",
        )
