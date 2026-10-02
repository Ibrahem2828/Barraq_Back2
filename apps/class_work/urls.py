from rest_framework.routers import SimpleRouter

from .views import (
    AdminAssignmentSubmissionViewSet,
    AdminClassAnnouncementViewSet,
    AdminClassAssignmentViewSet,
    AdminClassEventViewSet,
    AdminClassQuizAnswerViewSet,
    AdminClassQuizQuestionViewSet,
    AdminClassQuizViewSet,
    StudentClassAnnouncementViewSet,
    StudentClassAssignmentViewSet,
    StudentClassEventViewSet,
    StudentClassQuizAttemptViewSet,
    StudentClassQuizViewSet,
)

admin_router = SimpleRouter(use_regex_path=False)
admin_router.register(r'class-announcements', AdminClassAnnouncementViewSet, basename='admin-class-announcement')
admin_router.register(r'class-events', AdminClassEventViewSet, basename='admin-class-event')
admin_router.register(r'class-assignments', AdminClassAssignmentViewSet, basename='admin-class-assignment')
admin_router.register(r'assignment-submissions', AdminAssignmentSubmissionViewSet, basename='admin-assignment-submission')
admin_router.register(r'class-quizzes', AdminClassQuizViewSet, basename='admin-class-quiz')
admin_router.register(r'class-quiz-questions', AdminClassQuizQuestionViewSet, basename='admin-class-quiz-question')
admin_router.register(r'class-quiz-answers', AdminClassQuizAnswerViewSet, basename='admin-class-quiz-answer')

student_router = SimpleRouter(use_regex_path=False)
student_router.register(r'class-announcements', StudentClassAnnouncementViewSet, basename='class-announcement')
student_router.register(r'class-events', StudentClassEventViewSet, basename='class-event')
student_router.register(r'class-assignments', StudentClassAssignmentViewSet, basename='class-assignment')
student_router.register(r'class-quizzes', StudentClassQuizViewSet, basename='class-quiz')
student_router.register(r'class-quiz-attempts', StudentClassQuizAttemptViewSet, basename='class-quiz-attempt')

admin_urlpatterns = admin_router.urls
student_urlpatterns = student_router.urls
