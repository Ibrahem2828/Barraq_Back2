from django.contrib import admin

from .models import Summary


@admin.register(Summary)
class SummaryAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'title', 'source', 'collection', 'quality_score', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('user__email', 'title', 'short_summary')
