from django.contrib import admin

from .models import FlashcardReviewState, MistakeEntry


@admin.register(MistakeEntry)
class MistakeEntryAdmin(admin.ModelAdmin):
    list_display = ('user', 'topic', 'category', 'status', 'box', 'next_review_at')
    list_filter = ('status', 'category', 'box')
    search_fields = ('user__email', 'question_text', 'topic')


@admin.register(FlashcardReviewState)
class FlashcardReviewStateAdmin(admin.ModelAdmin):
    list_display = ('user', 'summary', 'flashcard_index', 'box', 'next_review_at')
    list_filter = ('box',)
    search_fields = ('user__email',)
