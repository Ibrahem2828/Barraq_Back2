from rest_framework.routers import SimpleRouter

from .views import StudentSourceCollectionViewSet, StudentSourceViewSet

router = SimpleRouter(use_regex_path=False)
router.register(r'student-sources', StudentSourceViewSet, basename='student-source')
router.register(
    r'student-source-collections',
    StudentSourceCollectionViewSet,
    basename='student-source-collection',
)

urlpatterns = router.urls
