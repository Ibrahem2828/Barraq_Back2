from django.contrib import admin

from .models import SupportMessage, SupportTicket


class SupportMessageInline(admin.TabularInline):
    model = SupportMessage
    extra = 0


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "user", "category", "priority", "status", "assigned_to", "created_at")
    list_filter = ("status", "priority", "category", "created_at")
    search_fields = ("subject", "user__email")
    inlines = [SupportMessageInline]
