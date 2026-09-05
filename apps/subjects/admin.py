from django.contrib import admin

from .models import EducationStage, Subject, UserSubject


@admin.register(EducationStage)
class EducationStageAdmin(admin.ModelAdmin):
    list_display = ('name', 'order', 'is_active', 'created_at')
    search_fields = ('name', 'description')
    list_filter = ('is_active',)
    ordering = ('order', 'id')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'education_stage',
        'grade_level',
        'is_active',
        'created_at',
    )
    search_fields = ('name', 'description', 'grade_level', 'education_stage__name')
    list_filter = ('is_active', 'education_stage', 'grade_level')
    autocomplete_fields = ('education_stage',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(UserSubject)
class UserSubjectAdmin(admin.ModelAdmin):
    list_display = ('user', 'subject', 'created_at')
    search_fields = ('user__email', 'user__full_name', 'subject__name')
    list_filter = ('subject__education_stage', 'subject')
    autocomplete_fields = ('user', 'subject')
    readonly_fields = ('created_at', 'updated_at')
