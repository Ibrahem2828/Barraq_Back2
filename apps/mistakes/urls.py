from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import FlashcardReviewView, MistakeEntryViewSet, ReviewQueueView

router = SimpleRouter(use_regex_path=False)
router.register(r'mistakes', MistakeEntryViewSet, basename='mistake-entry')

urlpatterns = [
    path('mistakes/review-queue/', ReviewQueueView.as_view(), name='mistakes-review-queue'),
    path('mistakes/flashcards/review/', FlashcardReviewView.as_view(), name='flashcard-review'),
    *router.urls,
]
