from django.urls import path

from .views import (
    EducationStageListView,
    SubjectListView,
    UserSubjectDestroyView,
    UserSubjectListCreateView,
)

urlpatterns = [
    path(
        'education-stages/',
        EducationStageListView.as_view(),
        name='education-stages',
    ),
    path('subjects/', SubjectListView.as_view(), name='subjects'),
    path(
        'users/subjects/',
        UserSubjectListCreateView.as_view(),
        name='user-subjects',
    ),
    path(
        'users/subjects/<int:pk>/',
        UserSubjectDestroyView.as_view(),
        name='user-subject-delete',
    ),
]
