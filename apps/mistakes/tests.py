from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.quizzes.models import AttemptStatusChoices, Choice, Question, Quiz, QuizAttempt, StudentAnswer
from apps.subjects.models import EducationStage, Subject
from apps.summaries.models import Summary

from .models import FlashcardReviewState, MistakeEntry
from .scheduling import REVIEW_INTERVALS_DAYS

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class MistakesNotebookTests(APITestCase):
    """دفتر أخطائي: every quiz error becomes a scheduled retry."""

    def setUp(self):
        self.alice = User.objects.create_user(email='alice@example.com', password='StrongPass123', full_name='Alice')
        self.bob = User.objects.create_user(email='bob@example.com', password='StrongPass123', full_name='Bob')
        stage = EducationStage.objects.create(name='Secondary', order=1)
        self.physics = Subject.objects.create(name='Physics', education_stage=stage, grade_level='10')
        self.client.force_authenticate(user=self.alice)

    def _submitted_attempt_with_one_wrong_answer(self, owner):
        quiz = Quiz.objects.create(user=owner, subject=self.physics, title='Unit 2', topic='Forces')
        q1 = Question.objects.create(quiz=quiz, text='F=?', order=1, points=1)
        Choice.objects.create(question=q1, text='ma', is_correct=True, order=1)
        wrong_choice = Choice.objects.create(question=q1, text='mv', is_correct=False, order=2)
        q2 = Question.objects.create(quiz=quiz, text='Unit of force?', order=2, points=1)
        Choice.objects.create(question=q2, text='Newton', is_correct=True, order=1)
        attempt = QuizAttempt.objects.create(
            user=owner, quiz=quiz, status=AttemptStatusChoices.SUBMITTED, submitted_at=timezone.now(),
            score=Decimal('1'), max_score=Decimal('2'), percentage=Decimal('50'),
        )
        StudentAnswer.objects.create(attempt=attempt, question=q1, selected_choice=wrong_choice, is_correct=False)
        StudentAnswer.objects.create(
            attempt=attempt, question=q2, selected_choice=q2.choices.first(), is_correct=True
        )
        return attempt

    # -- import ---------------------------------------------------------------
    def test_import_creates_one_entry_per_wrong_answer_and_is_idempotent(self):
        attempt = self._submitted_attempt_with_one_wrong_answer(self.alice)

        first = self.client.post(reverse('mistake-entry-import-from-attempt-action'), {'attempt': attempt.pk}, format='json')
        second = self.client.post(reverse('mistake-entry-import-from-attempt-action'), {'attempt': attempt.pk}, format='json')

        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        self.assertEqual(len(first.data), 1)
        self.assertEqual(first.data[0]['question_text'], 'F=?')
        self.assertEqual(first.data[0]['correct_answer_text'], 'ma')
        self.assertEqual(first.data[0]['student_answer_text'], 'mv')
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(second.data), 0)  # already imported, not duplicated
        self.assertEqual(MistakeEntry.objects.filter(user=self.alice).count(), 1)

    def test_cannot_import_someone_elses_attempt(self):
        attempt = self._submitted_attempt_with_one_wrong_answer(self.bob)

        response = self.client.post(reverse('mistake-entry-import-from-attempt-action'), {'attempt': attempt.pk}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(MistakeEntry.objects.exists())

    def test_cannot_import_an_in_progress_attempt(self):
        quiz = Quiz.objects.create(user=self.alice, subject=self.physics, title='Q', topic='t')
        attempt = QuizAttempt.objects.create(user=self.alice, quiz=quiz)

        response = self.client.post(reverse('mistake-entry-import-from-attempt-action'), {'attempt': attempt.pk}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # -- manual entries + ownership --------------------------------------------
    def test_manual_entry_and_listing_is_owner_scoped(self):
        response = self.client.post(
            reverse('mistake-entry-list'),
            {'question_text': 'Paper exam Q3', 'category': 'forgot', 'subject': self.physics.pk},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        self.client.force_authenticate(user=self.bob)
        response = self.client.get(reverse('mistake-entry-list'))
        rows = response.data['results'] if isinstance(response.data, dict) and 'results' in response.data else response.data
        self.assertEqual(len(rows), 0)

    def test_cannot_review_or_edit_someone_elses_entry(self):
        entry = MistakeEntry.objects.create(user=self.alice, question_text='x', next_review_at=timezone.now())
        self.client.force_authenticate(user=self.bob)

        review = self.client.post(reverse('mistake-entry-review', args=[entry.pk]), {'result': 'good'}, format='json')
        edit = self.client.patch(reverse('mistake-entry-detail', args=[entry.pk]), {'category': 'forgot'}, format='json')

        self.assertEqual(review.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(edit.status_code, status.HTTP_404_NOT_FOUND)

    # -- scheduling -------------------------------------------------------------
    def test_again_resets_box_and_good_advances_it(self):
        entry = MistakeEntry.objects.create(user=self.alice, question_text='x', box=2, next_review_at=timezone.now())

        self.client.post(reverse('mistake-entry-review', args=[entry.pk]), {'result': 'again'}, format='json')
        entry.refresh_from_db()
        self.assertEqual(entry.box, 0)

        self.client.post(reverse('mistake-entry-review', args=[entry.pk]), {'result': 'good'}, format='json')
        entry.refresh_from_db()
        self.assertEqual(entry.box, 1)
        self.assertEqual(entry.review_count, 2)
        self.assertIsNotNone(entry.last_reviewed_at)

    def test_reaching_the_top_box_on_good_masters_the_entry(self):
        entry = MistakeEntry.objects.create(
            user=self.alice, question_text='x', box=len(REVIEW_INTERVALS_DAYS) - 1, next_review_at=timezone.now()
        )

        response = self.client.post(reverse('mistake-entry-review', args=[entry.pk]), {'result': 'good'}, format='json')

        self.assertEqual(response.data['status'], 'mastered')
        entry.refresh_from_db()
        self.assertEqual(entry.status, MistakeEntry.Status.MASTERED)
        self.assertIsNotNone(entry.mastered_at)

    def test_a_mastered_entry_cannot_be_reviewed_again(self):
        entry = MistakeEntry.objects.create(
            user=self.alice, question_text='x', status=MistakeEntry.Status.MASTERED, next_review_at=timezone.now()
        )
        response = self.client.post(reverse('mistake-entry-review', args=[entry.pk]), {'result': 'good'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    # -- flashcards + merged queue ------------------------------------------------
    def test_flashcard_review_creates_state_and_advances_box(self):
        summary = Summary.objects.create(
            user=self.alice, title='S', flashcards=[{'front': 'Q1', 'back': 'A1'}, {'front': 'Q2', 'back': 'A2'}]
        )

        response = self.client.post(
            reverse('flashcard-review'), {'summary': summary.pk, 'flashcard_index': 0, 'result': 'good'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['box'], 1)
        self.assertEqual(FlashcardReviewState.objects.filter(user=self.alice, summary=summary).count(), 1)

    def test_cannot_review_a_flashcard_of_someone_elses_summary(self):
        summary = Summary.objects.create(user=self.bob, title='S', flashcards=[{'front': 'Q', 'back': 'A'}])
        response = self.client.post(
            reverse('flashcard-review'), {'summary': summary.pk, 'flashcard_index': 0, 'result': 'good'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_review_queue_merges_unreviewed_flashcards_and_due_mistakes(self):
        Summary.objects.create(user=self.alice, title='S', flashcards=[{'front': 'Q', 'back': 'A'}])
        MistakeEntry.objects.create(user=self.alice, question_text='x', next_review_at=timezone.now())
        # Not due yet: must not appear.
        MistakeEntry.objects.create(
            user=self.alice, question_text='future', next_review_at=timezone.now() + timezone.timedelta(days=30)
        )

        response = self.client.get(reverse('mistakes-review-queue'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        kinds = [row['kind'] for row in response.data]
        self.assertEqual(sorted(kinds), ['flashcard', 'mistake'])

    def test_review_queue_is_owner_scoped(self):
        MistakeEntry.objects.create(user=self.bob, question_text='x', next_review_at=timezone.now())
        response = self.client.get(reverse('mistakes-review-queue'))
        self.assertEqual(response.data, [])
