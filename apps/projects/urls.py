from rest_framework.routers import SimpleRouter

from .views import ProjectViewSet

router = SimpleRouter(use_regex_path=False)
router.register("projects", ProjectViewSet, basename="project")

urlpatterns = router.urls
