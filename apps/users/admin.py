from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .forms import CustomUserChangeForm, CustomUserCreationForm
from .models import PendingRegistration, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = User
    ordering = ('-created_at',)
    list_display = (
        'email',
        'full_name',
        'role',
        'phone_number',
        'is_active',
        'is_staff',
        'is_deleted',
        'created_at',
    )
    list_filter = (
        'role',
        'is_active',
        'is_staff',
        'is_superuser',
        'is_deleted',
        'created_at',
    )
    search_fields = ('email', 'full_name', 'phone_number')
    readonly_fields = ('created_at', 'updated_at', 'last_login', 'is_deleted', 'deleted_at')
    actions = ('restore_selected_users',)

    def get_queryset(self, request):
        # ``User.objects`` hides soft-deleted accounts; the admin should
        # still surface them (for support/audit/restore), not pretend
        # deletion made them disappear entirely.
        return User.all_objects.all()

    @admin.action(description='Restore selected users (undo soft delete)')
    def restore_selected_users(self, request, queryset):
        updated = queryset.filter(is_deleted=True).update(is_deleted=False, deleted_at=None)
        self.message_user(request, f'Restored {updated} user(s).')

    filter_horizontal = ('groups', 'user_permissions')
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Profile', {'fields': ('full_name', 'phone_number', 'role')}),
        (
            'Permissions',
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser',
                    'groups',
                    'user_permissions',
                )
            },
        ),
        ('Important Dates', {'fields': ('last_login', 'created_at', 'updated_at')}),
        ('Deletion', {'fields': ('is_deleted', 'deleted_at')}),
    )
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': (
                    'email',
                    'full_name',
                    'phone_number',
                    'role',
                    'password1',
                    'password2',
                    'is_active',
                ),
            },
        ),
    )


@admin.register(PendingRegistration)
class PendingRegistrationAdmin(admin.ModelAdmin):
    """Operational visibility without exposing password or OTP hashes."""

    list_display = (
        'normalized_email',
        'otp_expires_at',
        'otp_attempt_count',
        'otp_send_count',
        'last_otp_sent_at',
        'created_at',
    )
    search_fields = ('normalized_email',)
    readonly_fields = (
        'normalized_email',
        'full_name',
        'phone_number',
        'otp_expires_at',
        'otp_attempt_count',
        'otp_send_count',
        'otp_send_window_started_at',
        'last_otp_sent_at',
        'created_at',
        'updated_at',
    )
    exclude = ('password_hash', 'otp_hash')
