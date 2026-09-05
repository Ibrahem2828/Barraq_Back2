from django.contrib import admin

from .models import AdminPermission, AdminRole, AdminUserRole, AuditLog

admin.site.site_header = 'لوحة إدارة برّاق'
admin.site.site_title = 'Baraq Admin'
admin.site.index_title = 'إدارة منصة برّاق'


@admin.register(AdminPermission)
class AdminPermissionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'category', 'is_active', 'created_at')
    list_filter = ('category', 'is_active', 'created_at')
    search_fields = ('code', 'name', 'description')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AdminRole)
class AdminRoleAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_system', 'is_active', 'created_at')
    list_filter = ('is_system', 'is_active', 'created_at')
    search_fields = ('code', 'name', 'description')
    filter_horizontal = ('permissions',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AdminUserRole)
class AdminUserRoleAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'assigned_by', 'is_active', 'assigned_at')
    list_filter = ('role', 'is_active', 'assigned_at')
    search_fields = ('user__email', 'user__full_name', 'role__code', 'role__name')
    autocomplete_fields = ('user', 'role', 'assigned_by')
    readonly_fields = ('assigned_at',)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'actor', 'target_type', 'target_id', 'ip_address', 'created_at')
    list_filter = ('action', 'target_type', 'created_at')
    search_fields = ('action', 'actor__email', 'target_type', 'target_id')
    readonly_fields = (
        'actor',
        'action',
        'target_type',
        'target_id',
        'metadata',
        'ip_address',
        'user_agent',
        'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
