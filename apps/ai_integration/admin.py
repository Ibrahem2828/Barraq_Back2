from django.contrib import admin

from .models import AIFeedback, AIJob, AIWebhookEvent


@admin.register(AIJob)
class AIJobAdmin(admin.ModelAdmin):
    list_display = ('public_id', 'user', 'character', 'task_type', 'status', 'result_type', 'created_at')
    list_filter = ('character', 'task_type', 'status', 'credits_committed')
    search_fields = ('public_id', 'external_job_id', 'user__email', 'error_message')
    readonly_fields = ('public_id', 'created_at', 'updated_at')


@admin.register(AIFeedback)
class AIFeedbackAdmin(admin.ModelAdmin):
    list_display = ('id', 'job', 'user', 'rating', 'is_helpful', 'training_consent', 'created_at')
    list_filter = ('rating', 'is_helpful', 'feedback_type', 'training_consent')
    search_fields = ('job__public_id', 'user__email', 'comment')


@admin.register(AIWebhookEvent)
class AIWebhookEventAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'event_type', 'external_job_id', 'processed', 'received_at')
    list_filter = ('event_type', 'processed')
    search_fields = ('event_id', 'external_job_id', 'error_message')
