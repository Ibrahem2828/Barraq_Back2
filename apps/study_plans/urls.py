from rest_framework.routers import SimpleRouter

from .views import StudyPlanViewSet, StudyTaskViewSet

router = SimpleRouter(use_regex_path=False)
router.register(r'study-plans', StudyPlanViewSet, basename='study-plan')
router.register(r'study-tasks', StudyTaskViewSet, basename='study-task')

urlpatterns = router.urls
