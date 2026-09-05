from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.subjects.models import EducationStage, Subject

from .models import (
    AttemptStatusChoices,
    Choice,
    Question,
    QuestionBankItem,
    QuestionTypeChoices,
    Quiz,
    QuizAttempt,
    QuizStatusChoices,
)

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class QuizProductionFlowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='quiz-user@example.com',
            password='StrongPass123',
            full_name='Quiz User',
        )
        self.other_user = User.objects.create_user(
            email='other-quiz-user@example.com',
            password='StrongPass123',
            full_name='Other Quiz User',
        )
        self.stage = EducationStage.objects.create(name='Secondary', order=1)
        self.subject = Subject.objects.create(
            name='Physics',
            education_stage=self.stage,
            grade_level='12',
        )

    def authenticate(self, user=None):
        self.client.force_authenticate(user=user or self.user)

    def create_draft(self, *, user=None, title='Newton Laws'):
        user = user or self.user
        Quiz.objects.filter(user=user, title=title).delete()
        return Quiz.objects.create(
            user=user,
            subject=self.subject,
            title=title,
            topic='Forces',
            difficulty_level='medium',
            quiz_type='practice',
            generation_type='manual',
            status=QuizStatusChoices.DRAFT,
            time_limit_minutes=15,
        )

    def add_question(self, quiz, *, order=1, correct_index=0):
        question = Question.objects.create(
            quiz=quiz,
            text=f'Question {order}',
            question_type=QuestionTypeChoices.MCQ,
            difficulty_level='medium',
            explanation='Validated explanation',
            order=order,
            points=1,
        )
        choices = [
            Choice.objects.create(
                question=question,
                text=f'Choice {order}-{index + 1}',
                is_correct=index == correct_index,
                order=index + 1,
            )
            for index in range(4)
        ]
        quiz.questions_count = quiz.questions.count()
        quiz.save(update_fields=['questions_count', 'updated_at'])
        return question, choices

    def create_published_quiz(self, *, user=None, questions=2):
        quiz = self.create_draft(user=user)
        generated = [self.add_question(quiz, order=index + 1) for index in range(questions)]
        quiz.status = QuizStatusChoices.PUBLISHED
        quiz.save(update_fields=['status', 'updated_at'])
        return quiz, generated

    def test_quiz_list_requires_authentication(self):
        response = self.client.get(reverse('quiz-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_manual_quiz_is_created_as_empty_draft(self):
        self.authenticate()
        response = self.client.post(
            reverse('quiz-list'),
            {
                'subject': self.subject.id,
                'title': 'Manual Draft',
                'topic': 'Motion',
                'difficulty_level': 'medium',
                'quiz_type': 'practice',
                'generation_type': 'manual',
                'time_limit_minutes': 10,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], QuizStatusChoices.DRAFT)
        self.assertEqual(response.data['questions_count'], 0)

    def test_direct_ai_quiz_creation_is_rejected(self):
        self.authenticate()
        response = self.client.post(
            reverse('quiz-list'),
            {
                'subject': self.subject.id,
                'title': 'AI Quiz',
                'generation_type': 'ai',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_question_endpoint_validates_choices_and_updates_count(self):
        quiz = self.create_draft()
        self.authenticate()
        response = self.client.post(
            reverse('quiz-question-list'),
            {
                'quiz': quiz.id,
                'text': 'What is inertia?',
                'question_type': 'mcq',
                'difficulty_level': 'medium',
                'explanation': 'Newton first law.',
                'order': 1,
                'points': 2,
                'choices': [
                    {'text': 'A', 'is_correct': True, 'order': 1},
                    {'text': 'B', 'is_correct': False, 'order': 2},
                    {'text': 'C', 'is_correct': False, 'order': 3},
                    {'text': 'D', 'is_correct': False, 'order': 4},
                ],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        quiz.refresh_from_db()
        self.assertEqual(quiz.questions_count, 1)

    def test_question_endpoint_rejects_multiple_correct_choices(self):
        quiz = self.create_draft()
        self.authenticate()
        response = self.client.post(
            reverse('quiz-question-list'),
            {
                'quiz': quiz.id,
                'text': 'Invalid question',
                'question_type': 'mcq',
                'difficulty_level': 'medium',
                'order': 1,
                'points': 1,
                'choices': [
                    {'text': 'A', 'is_correct': True, 'order': 1},
                    {'text': 'B', 'is_correct': True, 'order': 2},
                ],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_publish_action_requires_validated_questions(self):
        quiz = self.create_draft()
        self.authenticate()
        empty_response = self.client.post(reverse('quiz-publish', args=[quiz.id]), {}, format='json')
        self.assertEqual(empty_response.status_code, status.HTTP_400_BAD_REQUEST)

        self.add_question(quiz)
        response = self.client.post(reverse('quiz-publish', args=[quiz.id]), {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], QuizStatusChoices.PUBLISHED)

    def test_patch_cannot_bypass_publish_validation(self):
        quiz = self.create_draft()
        self.authenticate()
        response = self.client.patch(
            reverse('quiz-detail', args=[quiz.id]),
            {'status': QuizStatusChoices.PUBLISHED},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_quiz_detail_does_not_expose_correct_choice(self):
        quiz, _ = self.create_published_quiz(questions=1)
        self.authenticate()
        response = self.client.get(reverse('quiz-detail', args=[quiz.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('is_correct', response.data['questions'][0]['choices'][0])

    def test_user_can_start_submit_and_read_result(self):
        quiz, generated = self.create_published_quiz(questions=2)
        self.authenticate()
        start = self.client.post(reverse('quiz-start', args=[quiz.id]), {}, format='json')
        self.assertEqual(start.status_code, status.HTTP_201_CREATED)
        attempt_id = start.data['attempt_id']

        answers = [
            {'question': question.id, 'selected_choice': choices[0].id}
            for question, choices in generated
        ]
        submit = self.client.post(
            reverse('quiz-attempt-submit', args=[attempt_id]),
            {'answers': answers},
            format='json',
        )
        self.assertEqual(submit.status_code, status.HTTP_200_OK)
        self.assertEqual(submit.data['correct_answers_count'], 2)
        self.assertEqual(str(submit.data['percentage']), '100.00')

        result = self.client.get(reverse('quiz-attempt-result', args=[attempt_id]))
        self.assertEqual(result.status_code, status.HTTP_200_OK)
        self.assertIn('correct_choice', result.data['answers'][0])

    def test_same_in_progress_attempt_is_reused(self):
        quiz, _ = self.create_published_quiz(questions=1)
        self.authenticate()
        first = self.client.post(reverse('quiz-start', args=[quiz.id]), {}, format='json')
        second = self.client.post(reverse('quiz-start', args=[quiz.id]), {}, format='json')
        self.assertEqual(first.data['attempt_id'], second.data['attempt_id'])

    def test_deleting_an_attempt_soft_deletes_it(self):
        quiz, _ = self.create_published_quiz(questions=1)
        attempt = QuizAttempt.objects.create(user=self.user, quiz=quiz)
        attempt_id = attempt.id

        attempt.delete()

        self.assertFalse(QuizAttempt.objects.filter(id=attempt_id).exists())
        deleted_attempt = QuizAttempt.all_objects.get(id=attempt_id)
        self.assertTrue(deleted_attempt.is_deleted)
        self.assertIsNotNone(deleted_attempt.deleted_at)

    def test_expired_attempt_ignores_late_answers(self):
        quiz, generated = self.create_published_quiz(questions=1)
        question, choices = generated[0]
        attempt = QuizAttempt.objects.create(
            user=self.user,
            quiz=quiz,
            status=AttemptStatusChoices.IN_PROGRESS,
            started_at=timezone.now() - timedelta(minutes=20),
            max_score=1,
        )
        self.authenticate()
        response = self.client.post(
            reverse('quiz-attempt-submit', args=[attempt.id]),
            {'answers': [{'question': question.id, 'selected_choice': choices[0].id}]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['correct_answers_count'], 0)
        self.assertEqual(response.data['unanswered_count'], 1)

    def test_user_cannot_access_another_users_quiz_or_attempt(self):
        quiz, _ = self.create_published_quiz(user=self.other_user, questions=1)
        attempt = QuizAttempt.objects.create(user=self.other_user, quiz=quiz)
        self.authenticate()
        self.assertEqual(
            self.client.get(reverse('quiz-detail', args=[quiz.id])).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.get(reverse('quiz-attempt-detail', args=[attempt.id])).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_public_and_private_question_bank_visibility(self):
        private_other = QuestionBankItem.objects.create(
            subject=self.subject,
            created_by=self.other_user,
            text='Private other',
            is_public=False,
        )
        public_other = QuestionBankItem.objects.create(
            subject=self.subject,
            created_by=self.other_user,
            text='Public other',
            is_public=True,
        )
        own = QuestionBankItem.objects.create(
            subject=self.subject,
            created_by=self.user,
            text='Own private',
            is_public=False,
        )
        self.authenticate()
        response = self.client.get(reverse('question-bank-list'))
        ids = {item['id'] for item in response.data['results']}
        self.assertNotIn(private_other.id, ids)
        self.assertIn(public_other.id, ids)
        self.assertIn(own.id, ids)
