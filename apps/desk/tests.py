from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.projects.models import Project
from apps.sources.models import StudentSource

from .models import DeskNote, FocusSession, SessionTask

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class DeskTests(APITestCase):
    """My Desk (مكتبي): owner-only, no AI, no cross-tenant surface."""

    def setUp(self):
        self.alice = User.objects.create_user(email='alice@example.com', password='StrongPass123', full_name='Alice')
        self.bob = User.objects.create_user(email='bob@example.com', password='StrongPass123', full_name='Bob')
        self.project = Project.objects.create(owner=self.alice, title='Physics')
        self.source = StudentSource.objects.create(
            user=self.alice,
            project=self.project,
            title='Unit 2',
            source_type=StudentSource.SourceType.PDF,
            file=SimpleUploadedFile('unit2.pdf', b'%PDF-1.4'),
            original_filename='unit2.pdf',
            file_size=8,
        )
        self.client.force_authenticate(user=self.alice)

    # -- notes --------------------------------------------------------------
    def test_create_and_list_notes_scoped_to_owner(self):
        response = self.client.post(
            reverse('desk-note-list'),
            {'source': self.source.pk, 'note_type': 'highlight', 'body': 'Newton 2nd law', 'anchor': {'page': 3}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        self.client.force_authenticate(user=self.bob)
        response = self.client.get(reverse('desk-note-list'))
        self.assertEqual(response.data['count'] if isinstance(response.data, dict) and 'count' in response.data else len(response.data), 0)

    def test_cannot_attach_a_note_to_someone_elses_source(self):
        self.client.force_authenticate(user=self.bob)
        response = self.client.post(
            reverse('desk-note-list'), {'source': self.source.pk, 'note_type': 'note', 'body': 'x'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_and_delete_own_note(self):
        note = DeskNote.objects.create(user=self.alice, source=self.source, body='v1')
        response = self.client.patch(reverse('desk-note-detail', args=[note.pk]), {'body': 'v2'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['body'], 'v2')

        response = self.client.delete(reverse('desk-note-detail', args=[note.pk]))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(DeskNote.objects.filter(pk=note.pk, is_deleted=False).exists())

    def test_cannot_touch_another_users_note(self):
        note = DeskNote.objects.create(user=self.alice, body='private')
        self.client.force_authenticate(user=self.bob)

        response = self.client.patch(reverse('desk-note-detail', args=[note.pk]), {'body': 'hacked'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # -- reading position -----------------------------------------------------
    def test_reading_position_round_trips_and_feeds_the_home_view(self):
        url = reverse('reading-position', args=[self.source.pk])
        response = self.client.put(url, {'position': {'page': 7, 'scroll': 120}}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        home = self.client.get(reverse('desk-home'))
        self.assertEqual(home.data['continue_reading']['source'], self.source.pk)
        self.assertEqual(home.data['continue_reading']['position']['page'], 7)

    def test_cannot_read_position_of_someone_elses_source(self):
        self.client.force_authenticate(user=self.bob)
        response = self.client.get(reverse('reading-position', args=[self.source.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # -- focus sessions ---------------------------------------------------------
    def test_starting_a_session_cancels_a_stale_running_one(self):
        first = self.client.post(reverse('focus-session-list'), {'task_label': 'Reading'}, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self.client.post(reverse('focus-session-list'), {'task_label': 'Problems'}, format='json')
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)

        first_session = FocusSession.objects.get(pk=first.data['id'])
        self.assertEqual(first_session.status, FocusSession.Status.CANCELLED)
        self.assertEqual(FocusSession.objects.filter(user=self.alice, status=FocusSession.Status.RUNNING).count(), 1)

    def test_stop_records_duration_and_cannot_stop_twice(self):
        created = self.client.post(reverse('focus-session-list'), {}, format='json')
        session_id = created.data['id']

        stopped = self.client.post(reverse('focus-session-stop', args=[session_id]))
        again = self.client.post(reverse('focus-session-stop', args=[session_id]))

        self.assertEqual(stopped.status_code, status.HTTP_200_OK)
        self.assertEqual(stopped.data['status'], 'completed')
        self.assertGreaterEqual(stopped.data['duration_seconds'], 0)
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)

    def test_home_view_surfaces_the_running_session(self):
        self.client.post(reverse('focus-session-list'), {'task_label': 'Reading'}, format='json')
        response = self.client.get(reverse('desk-home'))
        self.assertIsNotNone(response.data['running_focus_session'])
        self.assertEqual(response.data['running_focus_session']['task_label'], 'Reading')

    # -- session tasks -----------------------------------------------------------
    def test_today_tasks_crud_and_home_aggregate(self):
        response = self.client.post(
            reverse('session-task-list'), {'task_date': timezone.localdate().isoformat(), 'title': 'Read ch.2'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        task_id = response.data['id']

        done = self.client.patch(reverse('session-task-detail', args=[task_id]), {'is_done': True}, format='json')
        self.assertTrue(done.data['is_done'])

        home = self.client.get(reverse('desk-home'))
        self.assertEqual(len(home.data['today_tasks']), 1)

    def test_cannot_modify_another_users_task(self):
        task = SessionTask.objects.create(user=self.alice, task_date=timezone.localdate(), title='x')
        self.client.force_authenticate(user=self.bob)
        response = self.client.delete(reverse('session-task-detail', args=[task.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # -- preferences ---------------------------------------------------------
    def test_preferences_default_then_update(self):
        response = self.client.get(reverse('desk-preferences'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['theme'], '')

        response = self.client.patch(reverse('desk-preferences'), {'favorite_character': 'khota'}, format='json')
        self.assertEqual(response.data['favorite_character'], 'khota')
