from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.admin_dashboard.services import assign_roles_to_user, seed_default_rbac
from apps.organizations.models import ClassMembership, Classroom, Organization

from .models import HelpfulVote, KnowledgeThread

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class KnowledgeSquareTests(APITestCase):
    """ساحة المعرفة / صفي's lesson Q&A: classroom-scoped from day one."""

    def setUp(self):
        _, self.roles = seed_default_rbac()
        self.super_admin = User.objects.create_superuser(email='k-super@example.com', password='StrongPass123', full_name='Super')
        assign_roles_to_user(self.super_admin, [self.roles['super_admin']], self.super_admin, scopes=[{'scope_type': 'global'}])
        self.org = Organization.objects.create(name='School', created_by=self.super_admin)
        self.class_a = Classroom.objects.create(organization=self.org, name='10-A')
        self.class_b = Classroom.objects.create(organization=self.org, name='10-B')

        self.teacher = User.objects.create_user(email='teacher@example.com', password='StrongPass123', full_name='Teacher', role=User.Roles.ADMIN)
        assign_roles_to_user(self.teacher, [self.roles['class_supervisor']], self.super_admin, scopes=[{'scope_type': 'class', 'classroom': self.class_a}])

        self.alice = self._student('alice@example.com', self.class_a)
        self.bob = self._student('bob@example.com', self.class_a)
        self.carol = self._student('carol@example.com', self.class_b)
        self.client.force_authenticate(user=self.alice)

    def _student(self, email, classroom):
        user = User.objects.create_user(email=email, password='StrongPass123', full_name=email.split('@')[0])
        ClassMembership.objects.create(classroom=classroom, user=user, status='active')
        return user

    @staticmethod
    def rows(response):
        data = response.data
        return data['results'] if isinstance(data, dict) and 'results' in data else data

    def _create_thread(self, user, classroom, **extra):
        self.client.force_authenticate(user=user)
        payload = {'classroom': str(classroom.public_id), 'title': 'Why is F=ma?', 'body': 'I am confused.', **extra}
        return self.client.post(reverse('knowledge-thread-list'), payload, format='json')

    # -- visibility -----------------------------------------------------------
    def test_student_posts_a_question_classmates_see_but_outsiders_dont(self):
        created = self._create_thread(self.alice, self.class_a)
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        self.assertFalse(created.data['is_teacher_content'])

        self.client.force_authenticate(user=self.bob)
        classmate = self.client.get(reverse('knowledge-thread-list'))
        self.assertEqual(len(self.rows(classmate)), 1)

        self.client.force_authenticate(user=self.carol)
        outsider = self.client.get(reverse('knowledge-thread-list'))
        self.assertEqual(len(self.rows(outsider)), 0)
        detail = self.client.get(reverse('knowledge-thread-detail', args=[created.data['id']]))
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_post_to_a_class_i_do_not_belong_to(self):
        # Exists but is not the caller's: not-found, never a 403 that
        # confirms a classroom the caller cannot otherwise see.
        response = self._create_thread(self.alice, self.class_b)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(KnowledgeThread.objects.exists())

    def test_a_teachers_post_is_flagged_as_teacher_content(self):
        created = self._create_thread(self.teacher, self.class_a, thread_type='explanation', title='How to solve it')
        self.assertTrue(created.data['is_teacher_content'])

    # -- edit / delete rights ------------------------------------------------
    def test_only_the_author_or_a_moderator_may_edit_or_delete_a_thread(self):
        created = self._create_thread(self.alice, self.class_a)
        thread_id = created.data['id']

        self.client.force_authenticate(user=self.bob)
        stranger_edit = self.client.patch(reverse('knowledge-thread-detail', args=[thread_id]), {'status': 'archived'}, format='json')
        self.assertEqual(stranger_edit.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.teacher)
        teacher_edit = self.client.patch(reverse('knowledge-thread-detail', args=[thread_id]), {'status': 'archived'}, format='json')
        self.assertEqual(teacher_edit.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(user=self.alice)
        own_delete = self.client.delete(reverse('knowledge-thread-detail', args=[thread_id]))
        self.assertEqual(own_delete.status_code, status.HTTP_204_NO_CONTENT)

    # -- replies + accept -----------------------------------------------------
    def test_reply_then_accept_resolves_the_thread(self):
        thread_id = self._create_thread(self.alice, self.class_a).data['id']
        self.client.force_authenticate(user=self.bob)
        reply = self.client.post(reverse('knowledge-reply-list'), {'thread': thread_id, 'body': 'Newton\'s second law.'}, format='json')
        self.assertEqual(reply.status_code, status.HTTP_201_CREATED, reply.data)
        self.assertFalse(reply.data['is_teacher_reply'])

        self.client.force_authenticate(user=self.carol)
        stranger_reply = self.client.post(reverse('knowledge-reply-list'), {'thread': thread_id, 'body': 'x'}, format='json')
        self.assertEqual(stranger_reply.status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(user=self.alice)
        accepted = self.client.post(reverse('knowledge-thread-accept-reply', args=[thread_id]), {'reply': reply.data['id']}, format='json')
        self.assertEqual(accepted.status_code, status.HTTP_200_OK, accepted.data)
        self.assertEqual(accepted.data['status'], 'resolved')
        self.assertEqual(accepted.data['accepted_reply'], reply.data['id'])

    def test_a_stranger_cannot_accept_a_reply(self):
        thread_id = self._create_thread(self.alice, self.class_a).data['id']
        self.client.force_authenticate(user=self.bob)
        reply = self.client.post(reverse('knowledge-reply-list'), {'thread': thread_id, 'body': 'x'}, format='json').data
        response = self.client.post(reverse('knowledge-thread-accept-reply', args=[thread_id]), {'reply': reply['id']}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_only_author_or_moderator_may_edit_or_delete_a_reply(self):
        thread_id = self._create_thread(self.alice, self.class_a).data['id']
        self.client.force_authenticate(user=self.bob)
        reply_id = self.client.post(reverse('knowledge-reply-list'), {'thread': thread_id, 'body': 'x'}, format='json').data['id']

        self.client.force_authenticate(user=self.carol)
        stranger = self.client.delete(reverse('knowledge-reply-detail', args=[reply_id]))
        self.assertEqual(stranger.status_code, status.HTTP_404_NOT_FOUND)  # carol cannot even see the thread

        self.client.force_authenticate(user=self.alice)
        other_student = self.client.delete(reverse('knowledge-reply-detail', args=[reply_id]))
        self.assertEqual(other_student.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.teacher)
        moderator_delete = self.client.delete(reverse('knowledge-reply-detail', args=[reply_id]))
        self.assertEqual(moderator_delete.status_code, status.HTTP_204_NO_CONTENT)

    # -- helpful votes + save-for-review ---------------------------------------
    def test_helpful_toggles_and_is_idempotent_per_user(self):
        thread_id = self._create_thread(self.alice, self.class_a).data['id']
        self.client.force_authenticate(user=self.bob)
        reply_id = self.client.post(reverse('knowledge-reply-list'), {'thread': thread_id, 'body': 'x'}, format='json').data['id']

        self.client.force_authenticate(user=self.alice)
        first = self.client.post(reverse('knowledge-reply-helpful', args=[reply_id]), {}, format='json')
        second = self.client.post(reverse('knowledge-reply-helpful', args=[reply_id]), {}, format='json')

        self.assertEqual(first.data, {'is_helpful': True, 'helpful_count': 1})
        self.assertEqual(second.data, {'is_helpful': False, 'helpful_count': 0})
        self.assertEqual(HelpfulVote.objects.filter(reply_id=reply_id).count(), 0)

    def test_replies_are_ordered_by_helpfulness(self):
        thread_id = self._create_thread(self.alice, self.class_a).data['id']
        self.client.force_authenticate(user=self.bob)
        weak = self.client.post(reverse('knowledge-reply-list'), {'thread': thread_id, 'body': 'weak'}, format='json').data
        strong = self.client.post(reverse('knowledge-reply-list'), {'thread': thread_id, 'body': 'strong'}, format='json').data

        self.client.force_authenticate(user=self.alice)
        self.client.post(reverse('knowledge-reply-helpful', args=[strong['id']]), {}, format='json')

        listed = self.client.get(reverse('knowledge-reply-list'), {'thread': thread_id})
        self.assertEqual([row['id'] for row in self.rows(listed)], [strong['id'], weak['id']])

    def test_save_for_review_toggles(self):
        thread_id = self._create_thread(self.alice, self.class_a).data['id']
        saved = self.client.post(reverse('knowledge-thread-save', args=[thread_id]), {}, format='json')
        unsaved = self.client.post(reverse('knowledge-thread-save', args=[thread_id]), {}, format='json')
        self.assertEqual(saved.data, {'is_saved': True})
        self.assertEqual(unsaved.data, {'is_saved': False})

        self.client.post(reverse('knowledge-thread-save', args=[thread_id]), {}, format='json')
        mine = self.client.get(reverse('saved-thread-list'))
        self.assertEqual([row['id'] for row in self.rows(mine)], [thread_id])

    # -- staff moderation ----------------------------------------------------------
    def test_organization_manager_moderates_within_its_organization(self):
        manager = User.objects.create_user(email='manager@example.com', password='StrongPass123', full_name='M', role=User.Roles.ADMIN)
        assign_roles_to_user(manager, [self.roles['organization_manager']], self.super_admin, scopes=[{'scope_type': 'organization', 'organization': self.org}])
        thread_id = self._create_thread(self.alice, self.class_a).data['id']

        self.client.force_authenticate(user=manager)
        listed = self.client.get(reverse('admin-knowledge-thread-list'))
        self.assertEqual(len(self.rows(listed)), 1)
        deleted = self.client.delete(reverse('admin-knowledge-thread-detail', args=[thread_id]))
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)

    def test_class_supervisor_cannot_moderate_a_sibling_class(self):
        other_org = Organization.objects.create(name='Other', created_by=self.super_admin)
        other_class = Classroom.objects.create(organization=other_org, name='X')
        other_student = self._student('dave@example.com', other_class)
        thread_id = self._create_thread(other_student, other_class).data['id']

        self.client.force_authenticate(user=self.teacher)
        response = self.client.delete(reverse('admin-knowledge-thread-detail', args=[thread_id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_role_without_knowledge_permission_is_denied(self):
        finance = User.objects.create_user(email='finance@example.com', password='StrongPass123', full_name='F', role=User.Roles.ADMIN)
        assign_roles_to_user(finance, [self.roles['finance']], self.super_admin, scopes=[{'scope_type': 'global'}])
        self.client.force_authenticate(user=finance)
        response = self.client.get(reverse('admin-knowledge-thread-list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
