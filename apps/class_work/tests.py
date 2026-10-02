from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.admin_dashboard.services import assign_roles_to_user, seed_default_rbac
from apps.organizations.models import ClassMembership, Classroom, Organization, OrganizationMembership

from .models import (
    AssignmentSubmission,
    ClassAnnouncement,
    ClassAssignment,
    ClassQuiz,
    ClassQuizAttempt,
    ClassQuizChoice,
    ClassQuizQuestion,
)

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class ClassWorkTests(APITestCase):
    """صفي: announcements, assignments and teacher quizzes, class-scoped."""

    def setUp(self):
        _, self.roles = seed_default_rbac()
        self.super_admin = User.objects.create_superuser(
            email='cw-super@example.com', password='StrongPass123', full_name='Super'
        )
        assign_roles_to_user(self.super_admin, [self.roles['super_admin']], self.super_admin, scopes=[{'scope_type': 'global'}])
        self.org_a = Organization.objects.create(name='School A', created_by=self.super_admin)
        self.org_b = Organization.objects.create(name='School B', created_by=self.super_admin)
        self.class_a1 = Classroom.objects.create(organization=self.org_a, name='10-A')
        self.class_a2 = Classroom.objects.create(organization=self.org_a, name='10-B')
        self.class_b1 = Classroom.objects.create(organization=self.org_b, name='11-A')

        self.teacher_a1 = self._admin('teacher-a1@example.com', 'class_supervisor', {'scope_type': 'class', 'classroom': self.class_a1})
        self.manager_b = self._admin('manager-b@example.com', 'organization_manager', {'scope_type': 'organization', 'organization': self.org_b})

        self.alice = self._student('alice@example.com', self.org_a, self.class_a1)
        self.bob = self._student('bob@example.com', self.org_a, self.class_a2)
        self.carol = self._student('carol@example.com', self.org_b, self.class_b1)

    def _admin(self, email, role, scope):
        user = User.objects.create_user(email=email, password='StrongPass123', full_name=email, role=User.Roles.ADMIN)
        assign_roles_to_user(user, [self.roles[role]], self.super_admin, scopes=[scope])
        return user

    def _student(self, email, organization, classroom):
        user = User.objects.create_user(email=email, password='StrongPass123', full_name=email.split('@')[0])
        OrganizationMembership.objects.create(organization=organization, user=user, member_type='student', status='active')
        ClassMembership.objects.create(classroom=classroom, user=user, status='active')
        return user

    @staticmethod
    def rows(response):
        data = response.data
        return data['results'] if isinstance(data, dict) and 'results' in data else data

    # -- announcements + calendar (scoping is shared machinery) ------------------
    def test_teacher_posts_an_announcement_students_in_the_class_see(self):
        self.client.force_authenticate(user=self.teacher_a1)
        response = self.client.post(
            reverse('admin-class-announcement-list'),
            {'classroom': str(self.class_a1.public_id), 'title': 'Midterm moved', 'body': 'To Thursday.', 'pinned': True},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        self.client.force_authenticate(user=self.alice)
        mine = self.client.get(reverse('class-announcement-list'))
        self.assertEqual(len(self.rows(mine)), 1)

        self.client.force_authenticate(user=self.bob)
        other_class = self.client.get(reverse('class-announcement-list'))
        self.assertEqual(len(self.rows(other_class)), 0)

    def test_teacher_cannot_post_to_a_class_outside_its_scope(self):
        self.client.force_authenticate(user=self.teacher_a1)
        response = self.client.post(
            reverse('admin-class-announcement-list'),
            {'classroom': str(self.class_a2.public_id), 'title': 'x', 'body': 'y'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ClassAnnouncement.objects.exists())

    def test_role_without_class_work_permission_is_denied(self):
        finance = self._admin('finance@example.com', 'finance', {'scope_type': 'global'})
        self.client.force_authenticate(user=finance)
        response = self.client.get(reverse('admin-class-announcement-list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -- assignments ----------------------------------------------------------------
    def test_assignment_lifecycle_submit_and_grade(self):
        self.client.force_authenticate(user=self.teacher_a1)
        created = self.client.post(
            reverse('admin-class-assignment-list'),
            {'classroom': str(self.class_a1.public_id), 'title': 'Essay', 'max_points': 20, 'status': 'published'},
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        assignment_id = created.data['id']

        self.client.force_authenticate(user=self.alice)
        submitted = self.client.post(
            reverse('class-assignment-submit', args=[assignment_id]), {'text_response': 'My essay.'}, format='json'
        )
        self.assertEqual(submitted.status_code, status.HTTP_201_CREATED, submitted.data)
        self.assertEqual(submitted.data['status'], 'submitted')

        self.client.force_authenticate(user=self.teacher_a1)
        listed = self.client.get(reverse('admin-class-assignment-submissions', args=[assignment_id]))
        self.assertEqual(len(listed.data), 1)
        submission_id = AssignmentSubmission.objects.get(assignment_id=assignment_id, student=self.alice).pk

        graded = self.client.patch(
            reverse('admin-assignment-submission-grade', args=[submission_id]), {'grade': '18', 'feedback': 'Good.'}, format='json'
        )
        self.assertEqual(graded.status_code, status.HTTP_200_OK, graded.data)
        self.assertEqual(str(graded.data['grade']), '18.00')
        self.assertEqual(graded.data['status'], 'graded')

    def test_cannot_submit_to_an_assignment_outside_my_classes(self):
        self.client.force_authenticate(user=self.teacher_a1)
        created = self.client.post(
            reverse('admin-class-assignment-list'),
            {'classroom': str(self.class_a1.public_id), 'title': 'Essay', 'status': 'published'},
            format='json',
        )
        self.client.force_authenticate(user=self.carol)
        response = self.client.post(reverse('class-assignment-submit', args=[created.data['id']]), {'text_response': 'x'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_grade_cannot_exceed_max_points(self):
        assignment = ClassAssignment.objects.create(classroom=self.class_a1, title='A', max_points=10, status='published')
        submission = AssignmentSubmission.objects.create(
            assignment=assignment, student=self.alice, text_response='x', submitted_at=timezone.now()
        )
        self.client.force_authenticate(user=self.teacher_a1)
        response = self.client.patch(reverse('admin-assignment-submission-grade', args=[submission.pk]), {'grade': '99'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_teacher_cannot_grade_a_submission_outside_scope(self):
        assignment = ClassAssignment.objects.create(classroom=self.class_b1, title='A', max_points=10, status='published')
        submission = AssignmentSubmission.objects.create(
            assignment=assignment, student=self.carol, text_response='x', submitted_at=timezone.now()
        )
        self.client.force_authenticate(user=self.teacher_a1)
        response = self.client.patch(reverse('admin-assignment-submission-grade', args=[submission.pk]), {'grade': '5'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # -- class quizzes: authoring + taking ---------------------------------------
    def _published_quiz_with_mcq_and_written(self):
        quiz = ClassQuiz.objects.create(classroom=self.class_a1, title='Unit 2', status=ClassQuiz.Status.DRAFT)
        mcq = ClassQuizQuestion.objects.create(class_quiz=quiz, question_type='mcq', text='F=?', points=2, order=1)
        ClassQuizChoice.objects.create(question=mcq, text='ma', is_correct=True, order=1)
        ClassQuizChoice.objects.create(question=mcq, text='mv', is_correct=False, order=2)
        written = ClassQuizQuestion.objects.create(class_quiz=quiz, question_type='written', text='Explain inertia.', points=3, order=2)
        quiz.status = ClassQuiz.Status.PUBLISHED
        quiz.save(update_fields=['status'])
        return quiz, mcq, written

    def test_teacher_authors_a_quiz_with_nested_choices(self):
        self.client.force_authenticate(user=self.teacher_a1)
        quiz = self.client.post(
            reverse('admin-class-quiz-list'), {'classroom': str(self.class_a1.public_id), 'title': 'Unit 2', 'status': 'draft'}, format='json'
        )
        self.assertEqual(quiz.status_code, status.HTTP_201_CREATED, quiz.data)

        question = self.client.post(
            reverse('admin-class-quiz-question-list'),
            {
                'class_quiz': quiz.data['id'], 'question_type': 'mcq', 'text': 'F=?', 'points': 2, 'order': 1,
                'choices': [{'text': 'ma', 'is_correct': True, 'order': 1}, {'text': 'mv', 'is_correct': False, 'order': 2}],
            },
            format='json',
        )
        self.assertEqual(question.status_code, status.HTTP_201_CREATED, question.data)
        self.assertEqual(len(question.data['choices']), 2)

    def test_exactly_one_correct_choice_is_required(self):
        self.client.force_authenticate(user=self.teacher_a1)
        quiz = ClassQuiz.objects.create(classroom=self.class_a1, title='Q', status='draft')
        response = self.client.post(
            reverse('admin-class-quiz-question-list'),
            {
                'class_quiz': quiz.pk, 'question_type': 'mcq', 'text': 'x', 'order': 1,
                'choices': [{'text': 'a', 'is_correct': True}, {'text': 'b', 'is_correct': True}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_teacher_cannot_author_questions_outside_scope(self):
        self.client.force_authenticate(user=self.teacher_a1)
        other_quiz = ClassQuiz.objects.create(classroom=self.class_a2, title='Q', status='draft')
        response = self.client.post(
            reverse('admin-class-quiz-question-list'),
            {'class_quiz': other_quiz.pk, 'question_type': 'written', 'text': 'x', 'order': 1},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_only_sees_published_quizzes_of_own_classes(self):
        quiz, _mcq, _written = self._published_quiz_with_mcq_and_written()
        ClassQuiz.objects.create(classroom=self.class_a1, title='Draft', status=ClassQuiz.Status.DRAFT)

        self.client.force_authenticate(user=self.alice)
        listed = self.client.get(reverse('class-quiz-list'))
        self.assertEqual([row['id'] for row in self.rows(listed)], [quiz.pk])

        self.client.force_authenticate(user=self.bob)
        other = self.client.get(reverse('class-quiz-list'))
        self.assertEqual(len(self.rows(other)), 0)

    def test_full_take_flow_mcq_auto_graded_written_pending_then_teacher_grades(self):
        quiz, mcq, written = self._published_quiz_with_mcq_and_written()
        self.client.force_authenticate(user=self.alice)

        started = self.client.post(reverse('class-quiz-start', args=[quiz.pk]), {}, format='json')
        self.assertEqual(started.status_code, status.HTTP_201_CREATED)
        attempt_id = started.data['id']
        correct_choice = mcq.choices.get(is_correct=True)

        self.client.post(
            reverse('class-quiz-attempt-answer', args=[attempt_id]),
            {'question': mcq.pk, 'selected_choice': correct_choice.pk}, format='json',
        )
        self.client.post(
            reverse('class-quiz-attempt-answer', args=[attempt_id]),
            {'question': written.pk, 'text_answer': 'Objects resist changes in motion.'}, format='json',
        )
        submitted = self.client.post(reverse('class-quiz-attempt-submit', args=[attempt_id]), {}, format='json')

        self.assertEqual(submitted.data['score'], '2.00')  # only the mcq graded so far
        self.assertFalse(submitted.data['fully_graded'])
        self.assertIsNone(submitted.data['percentage'])

        self.client.force_authenticate(user=self.teacher_a1)
        written_answer = written.student_answers.get(attempt_id=attempt_id)
        graded = self.client.patch(
            reverse('admin-class-quiz-answer-grade', args=[written_answer.pk]), {'points_awarded': 3}, format='json'
        )
        self.assertEqual(graded.status_code, status.HTTP_200_OK, graded.data)

        attempt = ClassQuizAttempt.objects.get(pk=attempt_id)
        self.assertTrue(attempt.fully_graded)
        self.assertEqual(attempt.score, 5)
        self.assertEqual(attempt.percentage, 100)

    def test_cannot_answer_or_start_a_quiz_outside_my_classes(self):
        quiz, _mcq, _written = self._published_quiz_with_mcq_and_written()
        self.client.force_authenticate(user=self.carol)
        response = self.client.post(reverse('class-quiz-start', args=[quiz.pk]), {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_submitted_attempt_cannot_be_answered_again(self):
        quiz, mcq, _written = self._published_quiz_with_mcq_and_written()
        self.client.force_authenticate(user=self.alice)
        started = self.client.post(reverse('class-quiz-start', args=[quiz.pk]), {}, format='json')
        self.client.post(reverse('class-quiz-attempt-submit', args=[started.data['id']]), {}, format='json')

        response = self.client.post(
            reverse('class-quiz-attempt-answer', args=[started.data['id']]),
            {'question': mcq.pk, 'selected_choice': mcq.choices.first().pk}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
