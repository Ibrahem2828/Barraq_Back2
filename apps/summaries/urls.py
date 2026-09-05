from rest_framework.routers import SimpleRouter

from .views import SummaryViewSet

router = SimpleRouter(use_regex_path=False)
router.register('summaries', SummaryViewSet, basename='summary')
urlpatterns = router.urls
