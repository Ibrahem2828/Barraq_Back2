from django.urls import path

from .api_views import APIRootView
from .views import HealthCheckView, LivenessView, ProjectMetaView, ReadinessView

urlpatterns = [
    path("", APIRootView.as_view(), name="api-root"),
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path("health/live/", LivenessView.as_view(), name="health-live"),
    path("health/ready/", ReadinessView.as_view(), name="health-ready"),
    path("meta/", ProjectMetaView.as_view(), name="project-meta"),
]
