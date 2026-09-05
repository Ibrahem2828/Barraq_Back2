from django.db.models import Prefetch, Q

from .models import Choice, Question, QuestionBankItem, Quiz, QuizAttempt, StudentAnswer


def get_user_quizzes(user):
    question_queryset = Question.objects.order_by('order', 'id').prefetch_related(
        Prefetch('choices', queryset=Choice.objects.order_by('order', 'id'))
    )
    attempts_queryset = QuizAttempt.objects.filter(user=user).order_by('-started_at')

    return (
        Quiz.objects.select_related('user', 'subject', 'subject__education_stage')
        .prefetch_related(
            Prefetch('questions', queryset=question_queryset),
            Prefetch('attempts', queryset=attempts_queryset),
        )
        .filter(user=user)
    )


def get_user_quiz_detail(user, quiz_id):
    return get_user_quizzes(user).filter(id=quiz_id).first()


def get_user_attempts(user):
    answers_queryset = StudentAnswer.objects.select_related(
        'question',
        'selected_choice',
    ).order_by('question__order', 'id')

    return (
        QuizAttempt.objects.select_related(
            'user',
            'quiz',
            'quiz__subject',
            'quiz__subject__education_stage',
        )
        .prefetch_related(
            Prefetch(
                'quiz__questions',
                queryset=Question.objects.order_by('order', 'id').prefetch_related(
                    Prefetch('choices', queryset=Choice.objects.order_by('order', 'id'))
                ),
            ),
            Prefetch('answers', queryset=answers_queryset),
        )
        .filter(user=user)
    )


def get_attempt_detail(user, attempt_id):
    return get_user_attempts(user).filter(id=attempt_id).first()


def get_question_bank_items(user, filters=None):
    queryset = QuestionBankItem.objects.select_related(
        'subject',
        'subject__education_stage',
        'created_by',
    ).filter(is_active=True).filter(Q(is_public=True) | Q(created_by=user))

    filters = filters or {}

    subject = filters.get('subject')
    if subject:
        queryset = queryset.filter(subject_id=subject)

    difficulty_level = filters.get('difficulty_level')
    if difficulty_level:
        queryset = queryset.filter(difficulty_level=difficulty_level)

    question_type = filters.get('question_type')
    if question_type:
        queryset = queryset.filter(question_type=question_type)

    return queryset.order_by('-created_at')
