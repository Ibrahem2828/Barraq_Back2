from django.urls import path

from .views import WaitlistCountView, WaitlistJoinView

urlpatterns = [
    path("waitlist/", WaitlistJoinView.as_view(), name="waitlist-join"),
    path("waitlist/count/", WaitlistCountView.as_view(), name="waitlist-count"),
]
