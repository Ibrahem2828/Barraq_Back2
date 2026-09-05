from rest_framework.routers import SimpleRouter

from .views import QuestionBankViewSet, QuizAttemptViewSet, QuizQuestionViewSet, QuizViewSet

router = SimpleRouter(use_regex_path=False)
router.register(r'quizzes', QuizViewSet, basename='quiz')
router.register(r'quiz-questions', QuizQuestionViewSet, basename='quiz-question')
router.register(r'quiz-attempts', QuizAttemptViewSet, basename='quiz-attempt')
router.register(r'question-bank', QuestionBankViewSet, basename='question-bank')

urlpatterns = router.urls
