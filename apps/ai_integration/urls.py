from django.urls import URLPattern, URLResolver, path
from rest_framework.routers import SimpleRouter

from .views import AIApiRootView, AICapabilitiesView, AIJobViewSet, AIServiceHealthView

router = SimpleRouter(use_regex_path=False)
router.register('jobs', AIJobViewSet, basename='ai-job')

urlpatterns: list[URLPattern | URLResolver] = [
    path('', AIApiRootView.as_view(), name='ai-root'),
    path('capabilities/', AICapabilitiesView.as_view(), name='ai-capabilities'),
    path('service-health/', AIServiceHealthView.as_view(), name='ai-service-health'),
]
urlpatterns += router.urls
