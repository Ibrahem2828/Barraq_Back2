from django.urls import URLPattern, URLResolver, path
from rest_framework.routers import SimpleRouter

from apps.ai_integration.admin_api import AdminAIFeedbackViewSet, AdminAIJobViewSet, AdminAIWebhookEventViewSet
from apps.ai_integration.admin_results import AdminRecommendationViewSet, AdminSummaryViewSet, AdminTranscriptionViewSet
from apps.subjects.admin_api import AdminEducationStageViewSet, AdminSubjectViewSet
from apps.subscriptions.views import (
    AdminSubscriptionPlanViewSet,
    AdminSubscriptionUsageViewSet,
    AdminUserSubscriptionViewSet,
)
from apps.support.admin_api import AdminSupportTicketViewSet

from .views import (
    AdminAIUsageView,
    AdminApiRootView,
    AdminCharacterInteractionViewSet,
    AdminMeView,
    AdminOverviewView,
    AdminPermissionViewSet,
    AdminQuizAttemptViewSet,
    AdminQuizViewSet,
    AdminRoleViewSet,
    AdminSourceCollectionViewSet,
    AdminSourceViewSet,
    AdminStudyPlanViewSet,
    AdminUserViewSet,
    AuditLogViewSet,
    ManagedUserViewSet,
    SystemHealthView,
)

router = SimpleRouter(use_regex_path=False)
router.register(r'permissions', AdminPermissionViewSet, basename='admin-permission')
router.register(r'education-stages', AdminEducationStageViewSet, basename='admin-education-stage')
router.register(r'subjects', AdminSubjectViewSet, basename='admin-subject')
router.register(r'roles', AdminRoleViewSet, basename='admin-role')
router.register(r'admin-users', AdminUserViewSet, basename='admin-user')
router.register(r'users', ManagedUserViewSet, basename='admin-managed-user')
router.register(
    r'source-collections',
    AdminSourceCollectionViewSet,
    basename='admin-source-collection',
)
router.register(r'sources', AdminSourceViewSet, basename='admin-source')
router.register(r'study-plans', AdminStudyPlanViewSet, basename='admin-study-plan')
router.register(r'quizzes', AdminQuizViewSet, basename='admin-quiz')
router.register(r'quiz-attempts', AdminQuizAttemptViewSet, basename='admin-quiz-attempt')
router.register(
    r'character-interactions',
    AdminCharacterInteractionViewSet,
    basename='admin-character-interaction',
)
router.register(r'audit-logs', AuditLogViewSet, basename='admin-audit-log')
router.register(r'ai-jobs', AdminAIJobViewSet, basename='admin-ai-job')
router.register(r'ai-feedback', AdminAIFeedbackViewSet, basename='admin-ai-feedback')
router.register(r'ai-webhook-events', AdminAIWebhookEventViewSet, basename='admin-ai-webhook-event')
router.register(r'ai-recommendations', AdminRecommendationViewSet, basename='admin-ai-recommendation')
router.register(r'ai-summaries', AdminSummaryViewSet, basename='admin-ai-summary')
router.register(r'ai-transcriptions', AdminTranscriptionViewSet, basename='admin-ai-transcription')
router.register(r'support-tickets', AdminSupportTicketViewSet, basename='admin-support-ticket')
router.register(
    r'subscription-plans',
    AdminSubscriptionPlanViewSet,
    basename='admin-subscription-plan',
)
router.register(
    r'user-subscriptions',
    AdminUserSubscriptionViewSet,
    basename='admin-user-subscription',
)
router.register(
    r'subscription-usage',
    AdminSubscriptionUsageViewSet,
    basename='admin-subscription-usage',
)

urlpatterns: list[URLPattern | URLResolver] = [
    path('', AdminApiRootView.as_view(), name='admin-api-root'),
    path('me/', AdminMeView.as_view(), name='admin-me'),
    path('overview/', AdminOverviewView.as_view(), name='admin-overview'),
    path('ai-usage/', AdminAIUsageView.as_view(), name='admin-ai-usage'),
    path('system/health/', SystemHealthView.as_view(), name='admin-system-health'),
]
urlpatterns += router.urls
