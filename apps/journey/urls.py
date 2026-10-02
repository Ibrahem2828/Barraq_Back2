from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    AdminClassGoalViewSet,
    AdminPathStationViewSet,
    AdminSubjectPathViewSet,
    AdminUnlockableViewSet,
    AdminWeeklyChallengeViewSet,
    CompleteStationView,
    JourneyHomeView,
    StudentClassGoalViewSet,
    UnlockableListView,
    WeeklyChallengeListView,
)

admin_router = SimpleRouter(use_regex_path=False)
admin_router.register(r'journey/subject-paths', AdminSubjectPathViewSet, basename='admin-subject-path')
admin_router.register(r'journey/path-stations', AdminPathStationViewSet, basename='admin-path-station')
admin_router.register(r'journey/unlockables', AdminUnlockableViewSet, basename='admin-unlockable')
admin_router.register(r'journey/weekly-challenges', AdminWeeklyChallengeViewSet, basename='admin-weekly-challenge')
admin_router.register(r'class-goals', AdminClassGoalViewSet, basename='admin-class-goal')

student_router = SimpleRouter(use_regex_path=False)
student_router.register(r'class-goals', StudentClassGoalViewSet, basename='class-goal')

admin_urlpatterns = admin_router.urls
student_urlpatterns = [
    path('journey/', JourneyHomeView.as_view(), name='journey-home'),
    path('journey/stations/<int:station_id>/complete/', CompleteStationView.as_view(), name='journey-complete-station'),
    path('journey/unlockables/', UnlockableListView.as_view(), name='journey-unlockables'),
    path('journey/challenges/', WeeklyChallengeListView.as_view(), name='journey-challenges'),
    *student_router.urls,
]
