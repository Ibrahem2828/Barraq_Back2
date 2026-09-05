from django.contrib import admin

from .models import StudentSource, StudentSourceCollection, StudentSourceInteraction


@admin.register(StudentSourceCollection)
class StudentSourceCollectionAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'user',
        'subject',
        'status',
        'source_count',
        'created_at',
    )
    list_filter = ('status', 'subject', 'created_at')
    search_fields = ('name', 'description', 'user__email')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('user', 'subject')


@admin.register(StudentSource)
class StudentSourceAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'user',
        'subject',
        'collection',
        'source_type',
        'status',
        'file_size',
        'created_at',
    )
    list_filter = ('source_type', 'status', 'subject', 'collection', 'created_at')
    search_fields = ('title', 'original_filename', 'user__email', 'collection__name')
    readonly_fields = (
        'original_filename',
        'file_size',
        'mime_type',
        'extension',
        'created_at',
        'updated_at',
    )
    autocomplete_fields = ('user', 'subject', 'collection')


@admin.register(StudentSourceInteraction)
class StudentSourceInteractionAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'source',
        'collection',
        'character',
        'action',
        'status',
        'result_type',
        'result_id',
        'created_at',
    )
    list_filter = ('character', 'action', 'status', 'created_at')
    search_fields = ('user__email', 'source__title', 'collection__name', 'message')
    autocomplete_fields = ('user', 'source', 'collection')
    readonly_fields = ('created_at', 'updated_at')
