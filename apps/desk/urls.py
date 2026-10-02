from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    DeskHomeView,
    DeskNoteViewSet,
    DeskPreferenceView,
    FocusSessionViewSet,
    ReadingPositionView,
    SessionTaskViewSet,
)

router = SimpleRouter(use_regex_path=False)
router.register(r'desk/notes', DeskNoteViewSet, basename='desk-note')
router.register(r'desk/focus-sessions', FocusSessionViewSet, basename='focus-session')
router.register(r'desk/tasks', SessionTaskViewSet, basename='session-task')

urlpatterns = [
    path('desk/', DeskHomeView.as_view(), name='desk-home'),
    path('desk/preferences/', DeskPreferenceView.as_view(), name='desk-preferences'),
    path('desk/reading-position/<int:source_id>/', ReadingPositionView.as_view(), name='reading-position'),
    *router.urls,
]
