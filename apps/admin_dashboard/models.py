from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class AdminPermission(BaseModel):
    code = models.CharField(max_length=120, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=80, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('category', 'code')
        verbose_name = 'Admin Permission'
        verbose_name_plural = 'Admin Permissions'

    def __str__(self):
        return self.code


class AdminRole(BaseModel):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    permissions = models.ManyToManyField(
        AdminPermission,
        related_name='roles',
        blank=True,
    )

    class Meta:
        ordering = ('name',)
        verbose_name = 'Admin Role'
        verbose_name_plural = 'Admin Roles'

    def __str__(self):
        return self.name


class AdminUserRole(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='admin_user_roles',
    )
    role = models.ForeignKey(
        AdminRole,
        on_delete=models.CASCADE,
        related_name='user_roles',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='assigned_admin_roles',
        null=True,
        blank=True,
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('-assigned_at',)
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'role'],
                name='unique_admin_user_role',
            )
        ]
        verbose_name = 'Admin User Role'
        verbose_name_plural = 'Admin User Roles'

    def __str__(self):
        return f'{self.user_id} - {self.role.code}'


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='admin_audit_logs',
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=120, db_index=True)
    target_type = models.CharField(max_length=120, blank=True)
    target_id = models.CharField(max_length=120, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'

    def __str__(self):
        return f'{self.action} - {self.created_at:%Y-%m-%d %H:%M:%S}'
