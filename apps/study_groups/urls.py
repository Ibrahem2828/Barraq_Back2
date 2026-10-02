from rest_framework.routers import SimpleRouter

from .views import GroupGoalViewSet, GroupMessageViewSet, GroupSessionViewSet, GroupTaskViewSet, StudyGroupViewSet

router = SimpleRouter(use_regex_path=False)
router.register(r'study-groups', StudyGroupViewSet, basename='study-group')
router.register(r'group-goals', GroupGoalViewSet, basename='group-goal')
router.register(r'group-tasks', GroupTaskViewSet, basename='group-task')
router.register(r'group-sessions', GroupSessionViewSet, basename='group-session')
router.register(r'group-messages', GroupMessageViewSet, basename='group-message')

urlpatterns = router.urls
