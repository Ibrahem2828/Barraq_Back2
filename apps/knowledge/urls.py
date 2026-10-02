from rest_framework.routers import SimpleRouter

from .views import (
    AdminKnowledgeReplyViewSet,
    AdminKnowledgeThreadViewSet,
    KnowledgeReplyViewSet,
    KnowledgeThreadViewSet,
    MySavedThreadsView,
)

admin_router = SimpleRouter(use_regex_path=False)
admin_router.register(r'knowledge-threads', AdminKnowledgeThreadViewSet, basename='admin-knowledge-thread')
admin_router.register(r'knowledge-replies', AdminKnowledgeReplyViewSet, basename='admin-knowledge-reply')

student_router = SimpleRouter(use_regex_path=False)
student_router.register(r'knowledge-threads', KnowledgeThreadViewSet, basename='knowledge-thread')
student_router.register(r'knowledge-replies', KnowledgeReplyViewSet, basename='knowledge-reply')
student_router.register(r'my/saved-threads', MySavedThreadsView, basename='saved-thread')

admin_urlpatterns = admin_router.urls
student_urlpatterns = student_router.urls
