from django.contrib import admin

from .models import SubscriptionEvent, SubscriptionPlan, SubscriptionUsage, UserSubscription


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'price', 'billing_interval', 'is_active', 'is_public', 'sort_order')
    search_fields = ('code', 'name')
    list_filter = ('billing_interval', 'is_active', 'is_public')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'status', 'current_period_end', 'provider', 'updated_at')
    search_fields = ('user__email', 'user__full_name', 'plan__code', 'plan__name')
    list_filter = ('status', 'plan', 'provider')
    autocomplete_fields = ('user', 'plan')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(SubscriptionUsage)
class SubscriptionUsageAdmin(admin.ModelAdmin):
    list_display = ('user', 'period_start', 'period_end', 'sources_uploaded', 'collections_created', 'ai_requests_used')
    search_fields = ('user__email', 'user__full_name')
    list_filter = ('period_start', 'period_end')
    autocomplete_fields = ('user', 'subscription')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(admin.ModelAdmin):
    list_display = ('user', 'subscription', 'event_type', 'created_at')
    search_fields = ('user__email', 'event_type')
    list_filter = ('event_type', 'created_at')
    readonly_fields = ('user', 'subscription', 'event_type', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False
