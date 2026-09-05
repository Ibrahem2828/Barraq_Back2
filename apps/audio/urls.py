from rest_framework.routers import SimpleRouter

from .views import TranscriptionViewSet

router = SimpleRouter(use_regex_path=False)
router.register('transcriptions', TranscriptionViewSet, basename='transcription')
urlpatterns = router.urls
