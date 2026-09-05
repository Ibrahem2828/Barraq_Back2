from django.contrib import admin

from .models import StudyPlan, StudyPlanProgressLog, StudyTask


class StudyTaskInline(admin.TabularInline):
    model = StudyTask
    extra = 0
    fields = (
        'title',
        'task_date',
        'estimated_minutes',
        'priority',
        'status',
        'order',
        'completed_at',
    )
    readonly_fields = ('completed_at', 'created_at', 'updated_at')
    show_change_link = True


@admin.register(StudyPlan)
class StudyPlanAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'user',
        'subject',
        'status',
        'generation_type',
        'difficulty_level',
        'completion_percentage',
        'start_date',
        'end_date',
        'created_at',
    )
    search_fields = ('title', 'goal', 'description', 'user__email', 'subject__name')
    list_filter = (
        'status',
        'generation_type',
        'difficulty_level',
        'subject',
        'subject__education_stage',
    )
    readonly_fields = ('completion_percentage', 'ai_request_id', 'created_at', 'updated_at')
    autocomplete_fields = ('user', 'subject')
    inlines = [StudyTaskInline]


@admin.register(StudyTask)
class StudyTaskAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'plan',
        'task_date',
        'estimated_minutes',
        'priority',
        'status',
        'order',
        'completed_at',
    )
    search_fields = ('title', 'description', 'plan__title', 'plan__user__email')
    list_filter = ('status', 'priority', 'task_date', 'plan__subject')
    readonly_fields = ('completed_at', 'created_at', 'updated_at')
    autocomplete_fields = ('plan',)


@admin.register(StudyPlanProgressLog)
class StudyPlanProgressLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'user', 'plan', 'task', 'created_at')
    search_fields = ('user__email', 'plan__title', 'task__title', 'action')
    list_filter = ('action', 'created_at')
    readonly_fields = ('user', 'plan', 'task', 'action', 'metadata', 'created_at')
    autocomplete_fields = ('user', 'plan', 'task')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
