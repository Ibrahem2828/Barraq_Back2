from rest_framework.routers import SimpleRouter

from .views import StudentRecommendationViewSet

router = SimpleRouter(use_regex_path=False)
router.register('recommendations', StudentRecommendationViewSet, basename='recommendation')
urlpatterns = router.urls
