from django.contrib import admin

from .models import Transcription


@admin.register(Transcription)
class TranscriptionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'source', 'title', 'language', 'duration_seconds', 'created_at')
    list_filter = ('language', 'created_at')
    search_fields = ('user__email', 'title', 'full_transcript')
