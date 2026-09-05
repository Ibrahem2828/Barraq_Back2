from rest_framework.routers import SimpleRouter

from .views import SupportTicketViewSet

router = SimpleRouter(use_regex_path=False)
router.register("support/tickets", SupportTicketViewSet, basename="support-ticket")
urlpatterns = router.urls
