from django.contrib import admin

from .models import StudentRecommendation


@admin.register(StudentRecommendation)
class StudentRecommendationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'subject', 'title', 'overall_score', 'is_read', 'created_at')
    list_filter = ('is_read', 'subject')
    search_fields = ('user__email', 'title', 'summary')
