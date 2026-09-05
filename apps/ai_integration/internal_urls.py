from django.urls import path

from .views import (
    AIWebhookView,
    InternalCollectionManifestView,
    InternalSourceDownloadView,
    InternalSourceManifestView,
    InternalUserContextView,
)

urlpatterns = [
    path('webhooks/jobs/', AIWebhookView.as_view(), name='ai-webhook-jobs'),
    path('sources/<int:pk>/manifest/', InternalSourceManifestView.as_view(), name='ai-source-manifest'),
    path('sources/<int:pk>/download/', InternalSourceDownloadView.as_view(), name='ai-source-download'),
    path('collections/<int:pk>/manifest/', InternalCollectionManifestView.as_view(), name='ai-collection-manifest'),
    path('users/<int:pk>/context/', InternalUserContextView.as_view(), name='ai-user-context'),
]
