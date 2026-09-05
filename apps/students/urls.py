from django.urls import path

from .views import StudentProfileDetailView, StudentProfileSetupView

urlpatterns = [
    path(
        'students/setup-profile/',
        StudentProfileSetupView.as_view(),
        name='student-setup-profile',
    ),
    path(
        'students/profile/',
        StudentProfileDetailView.as_view(),
        name='student-profile',
    ),
]
