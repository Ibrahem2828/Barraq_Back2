from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import GroupMembership, GroupTask, StudyGroup

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class StudyGroupTests(APITestCase):
    """رفاق برّاق: small, invite-only, no public discovery."""

    def setUp(self):
        self.alice = User.objects.create_user(email='alice@example.com', password='StrongPass123', full_name='Alice')
        self.bob = User.objects.create_user(email='bob@example.com', password='StrongPass123', full_name='Bob')
        self.carol = User.objects.create_user(email='carol@example.com', password='StrongPass123', full_name='Carol')
        self.client.force_authenticate(user=self.alice)

    # -- create / join / leave ----------------------------------------------
    def test_creating_a_group_makes_the_creator_its_owner(self):
        response = self.client.post(reverse('study-group-list'), {'name': 'Physics crew', 'max_members': 4}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['my_role'], 'owner')
        self.assertTrue(response.data['invite_code'])

    def test_join_by_code_then_appears_in_my_groups(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')

        self.client.force_authenticate(user=self.bob)
        joined = self.client.post(reverse('study-group-join'), {'invite_code': group.invite_code}, format='json')
        self.assertEqual(joined.status_code, status.HTTP_200_OK, joined.data)
        self.assertEqual(joined.data['my_role'], 'member')

        listed = self.client.get(reverse('study-group-list'))
        rows = listed.data['results'] if isinstance(listed.data, dict) and 'results' in listed.data else listed.data
        self.assertEqual([row['id'] for row in rows], [group.pk])

    def test_a_full_group_refuses_a_new_join(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=1)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')
        self.client.force_authenticate(user=self.bob)
        response = self.client.post(reverse('study-group-join'), {'invite_code': group.invite_code}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_wrong_invite_code_is_refused(self):
        response = self.client.post(reverse('study-group-join'), {'invite_code': 'NOPE1234'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_non_member_cannot_see_or_act_on_the_group(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')
        self.client.force_authenticate(user=self.carol)
        response = self.client.get(reverse('study-group-detail', args=[group.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_cannot_leave_and_member_can(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')
        GroupMembership.objects.create(group=group, user=self.bob, role='member')

        owner_leaves = self.client.post(reverse('study-group-leave', args=[group.pk]), {}, format='json')
        self.assertEqual(owner_leaves.status_code, status.HTTP_400_BAD_REQUEST)

        self.client.force_authenticate(user=self.bob)
        member_leaves = self.client.post(reverse('study-group-leave', args=[group.pk]), {}, format='json')
        self.assertEqual(member_leaves.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(GroupMembership.objects.get(group=group, user=self.bob).status, 'left')

    # -- ban ----------------------------------------------------------------
    def test_owner_bans_a_member_who_then_cannot_rejoin(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')
        GroupMembership.objects.create(group=group, user=self.bob, role='member')

        banned = self.client.post(reverse('study-group-ban', args=[group.pk]), {'user': self.bob.pk, 'reason': 'spam'}, format='json')
        self.assertEqual(banned.status_code, status.HTTP_204_NO_CONTENT)

        self.client.force_authenticate(user=self.bob)
        rejoin = self.client.post(reverse('study-group-join'), {'invite_code': group.invite_code}, format='json')
        self.assertEqual(rejoin.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_cannot_ban_another_member(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')
        GroupMembership.objects.create(group=group, user=self.bob, role='member')
        GroupMembership.objects.create(group=group, user=self.carol, role='member')

        self.client.force_authenticate(user=self.bob)
        response = self.client.post(reverse('study-group-ban', args=[group.pk]), {'user': self.carol.pk}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # -- goals / tasks (creator-or-owner may edit) -------------------------------
    def test_any_member_creates_a_goal_but_only_creator_or_owner_edits_it(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')
        GroupMembership.objects.create(group=group, user=self.bob, role='member')
        GroupMembership.objects.create(group=group, user=self.carol, role='member')

        self.client.force_authenticate(user=self.bob)
        created = self.client.post(reverse('group-task-list'), {'group': group.pk, 'title': 'Finish chapter 3'}, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        task_id = created.data['id']

        self.client.force_authenticate(user=self.carol)
        edited_by_stranger = self.client.patch(reverse('group-task-detail', args=[task_id]), {'is_done': True}, format='json')
        self.assertEqual(edited_by_stranger.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.alice)
        edited_by_owner = self.client.patch(reverse('group-task-detail', args=[task_id]), {'is_done': True}, format='json')
        self.assertEqual(edited_by_owner.status_code, status.HTTP_200_OK)
        self.assertTrue(GroupTask.objects.get(pk=task_id).is_done)

    def test_tasks_require_a_group_and_are_scoped_to_it(self):
        response = self.client.get(reverse('group-task-list'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # -- synced session timer -----------------------------------------------------
    def test_session_start_and_status_computes_remaining_time(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')

        created = self.client.post(
            reverse('group-session-list'), {'group': group.pk, 'scheduled_at': timezone.now().isoformat(), 'duration_minutes': 25},
            format='json',
        )
        session_id = created.data['id']
        started = self.client.post(reverse('group-session-start', args=[session_id]), {}, format='json')
        self.assertEqual(started.data['status'], 'active')

        status_response = self.client.get(reverse('group-session-status', args=[session_id]))
        self.assertEqual(status_response.data['status'], 'active')
        self.assertLessEqual(status_response.data['remaining_seconds'], 25 * 60)
        self.assertGreater(status_response.data['remaining_seconds'], 25 * 60 - 5)

    def test_ending_a_session_records_a_summary(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')
        GroupTask.objects.create(group=group, title='t', is_done=True, created_by=self.alice)

        created = self.client.post(
            reverse('group-session-list'), {'group': group.pk, 'scheduled_at': timezone.now().isoformat(), 'duration_minutes': 25},
            format='json',
        )
        self.client.post(reverse('group-session-start', args=[created.data['id']]), {}, format='json')
        ended = self.client.post(reverse('group-session-end', args=[created.data['id']]), {}, format='json')

        self.assertEqual(ended.data['status'], 'completed')
        self.assertEqual(ended.data['summary']['tasks_completed_count'], 1)

    # -- messages + attachments + reporting ---------------------------------------
    def test_message_with_an_attached_source_must_be_owned_by_sender(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.sources.models import StudentSource

        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')
        GroupMembership.objects.create(group=group, user=self.bob, role='member')
        bobs_source = StudentSource.objects.create(
            user=self.bob, title='x', source_type='pdf', file=SimpleUploadedFile('x.pdf', b'%PDF-1.4'),
            original_filename='x.pdf', file_size=8,
        )

        response = self.client.post(
            reverse('group-message-list'), {'group': group.pk, 'body': 'look', 'attached_source': bobs_source.pk}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reporting_a_message_twice_by_the_same_member_does_not_duplicate(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')
        GroupMembership.objects.create(group=group, user=self.bob, role='member')
        self.client.force_authenticate(user=self.bob)
        message = self.client.post(reverse('group-message-list'), {'group': group.pk, 'body': 'hi'}, format='json').data

        first = self.client.post(reverse('group-message-report', args=[message['id']]), {'reason': 'spam'}, format='json')
        second = self.client.post(reverse('group-message-report', args=[message['id']]), {'reason': 'spam'}, format='json')

        self.assertEqual(first.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(second.status_code, status.HTTP_204_NO_CONTENT)
        from .models import MessageReport

        self.assertEqual(MessageReport.objects.filter(message_id=message['id']).count(), 1)

    def test_enough_distinct_reports_auto_flags_the_message(self):
        group = StudyGroup.objects.create(name='G', created_by=self.alice, max_members=4)
        GroupMembership.objects.create(group=group, user=self.alice, role='owner')
        GroupMembership.objects.create(group=group, user=self.bob, role='member')
        GroupMembership.objects.create(group=group, user=self.carol, role='member')
        self.client.force_authenticate(user=self.bob)
        message = self.client.post(reverse('group-message-list'), {'group': group.pk, 'body': 'spam'}, format='json').data

        self.client.force_authenticate(user=self.alice)
        self.client.post(reverse('group-message-report', args=[message['id']]), {}, format='json')
        self.client.force_authenticate(user=self.carol)
        self.client.post(reverse('group-message-report', args=[message['id']]), {}, format='json')

        from .models import GroupMessage

        self.assertTrue(GroupMessage.objects.get(pk=message['id']).is_flagged)
