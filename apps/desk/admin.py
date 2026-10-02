from django.contrib import admin

from .models import DeskNote, DeskPreference, FocusSession, ReadingPosition, SessionTask


@admin.register(DeskNote)
class DeskNoteAdmin(admin.ModelAdmin):
    list_display = ('user', 'note_type', 'source', 'created_at')
    list_filter = ('note_type', 'created_at')
    search_fields = ('user__email', 'body')


@admin.register(ReadingPosition)
class ReadingPositionAdmin(admin.ModelAdmin):
    list_display = ('user', 'source', 'updated_at')
    search_fields = ('user__email',)


@admin.register(FocusSession)
class FocusSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'started_at', 'duration_seconds')
    list_filter = ('status',)
    search_fields = ('user__email',)


@admin.register(SessionTask)
class SessionTaskAdmin(admin.ModelAdmin):
    list_display = ('user', 'task_date', 'title', 'is_done')
    list_filter = ('is_done', 'task_date')
    search_fields = ('user__email', 'title')


@admin.register(DeskPreference)
class DeskPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'theme', 'favorite_character')
