from django.urls import path
from rest_framework.routers import SimpleRouter

from .library_api import AdminClassLibraryViewSet, StudentClassLibraryViewSet
from .views import (
    ClassroomViewSet,
    InvitationViewSet,
    JoinConfirmView,
    JoinPreviewView,
    JoinRequestViewSet,
    MyMembershipsView,
    OrganizationViewSet,
)

router = SimpleRouter(use_regex_path=False)
router.register(r"organizations", OrganizationViewSet, basename="organization")
router.register(r"classes", ClassroomViewSet, basename="classroom")
router.register(r"invitations", InvitationViewSet, basename="invitation")
router.register(r"join-requests", JoinRequestViewSet, basename="join-request")
router.register(r"class-library", AdminClassLibraryViewSet, basename="admin-class-library")

admin_urlpatterns = router.urls

student_router = SimpleRouter(use_regex_path=False)
student_router.register(r"class-library", StudentClassLibraryViewSet, basename="class-library")

# Learner-facing: authenticated, throttled, and scoped to request.user rather
# than to an admin grant.
student_urlpatterns = [
    path("join/preview/", JoinPreviewView.as_view(), name="join-preview"),
    path("join/confirm/", JoinConfirmView.as_view(), name="join-confirm"),
    path("my/memberships/", MyMembershipsView.as_view(), name="my-memberships"),
    *student_router.urls,
]
