import logging

from django.contrib.auth import get_user_model
from django.db import models, transaction

from .models import AdminPermission, AdminRole, AdminUserRole, AuditLog

logger = logging.getLogger(__name__)

SUPER_ADMIN_ROLE = 'super_admin'

DEFAULT_PERMISSIONS = [
    ('dashboard.view', 'Dashboard', 'View dashboard'),
    ('users.view', 'Users', 'View users'),
    ('users.create', 'Users', 'Create users'),
    ('users.update', 'Users', 'Update users'),
    ('users.suspend', 'Users', 'Suspend users'),
    ('users.activate', 'Users', 'Activate users'),
    ('users.delete', 'Users', 'Delete users'),
    ('admins.view', 'Admins', 'View admins'),
    ('admins.create', 'Admins', 'Create admins'),
    ('admins.update', 'Admins', 'Update admins'),
    ('admins.delete', 'Admins', 'Delete admins'),
    ('admins.assign_roles', 'Admins', 'Assign admin roles'),
    ('admins.assign_permissions', 'Admins', 'Assign admin permissions'),
    ('roles.view', 'Roles', 'View roles'),
    ('roles.create', 'Roles', 'Create roles'),
    ('roles.update', 'Roles', 'Update roles'),
    ('roles.delete', 'Roles', 'Delete roles'),
    ('students.view', 'Students', 'View students'),
    ('students.update', 'Students', 'Update students'),
    ('education_stages.view', 'Content', 'View education stages'),
    ('education_stages.create', 'Content', 'Create education stages'),
    ('education_stages.update', 'Content', 'Update education stages'),
    ('education_stages.delete', 'Content', 'Delete education stages'),
    ('subjects.view', 'Content', 'View subjects'),
    ('subjects.create', 'Content', 'Create subjects'),
    ('subjects.update', 'Content', 'Update subjects'),
    ('subjects.delete', 'Content', 'Delete subjects'),
    ('sources.view', 'Sources', 'View sources'),
    ('sources.delete', 'Sources', 'Delete sources'),
    ('sources.moderate', 'Sources', 'Moderate sources'),
    ('collections.view', 'Sources', 'View collections'),
    ('collections.delete', 'Sources', 'Delete collections'),
    ('collections.moderate', 'Sources', 'Moderate collections'),
    ('study_plans.view', 'Study', 'View study plans'),
    ('study_plans.delete', 'Study', 'Delete study plans'),
    ('study_tasks.view', 'Study', 'View study tasks'),
    ('quizzes.view', 'Quizzes', 'View quizzes'),
    ('quizzes.moderate', 'Quizzes', 'Moderate quizzes'),
    ('quizzes.delete', 'Quizzes', 'Delete quizzes'),
    ('quiz_attempts.view', 'Quizzes', 'View quiz attempts'),
    ('character_interactions.view', 'Characters', 'View character interactions'),
    ('analytics.view', 'Analytics', 'View analytics'),
    ('ai_jobs.view', 'AI', 'View AI jobs'),
    ('ai_jobs.cancel', 'AI', 'Cancel AI jobs'),
    ('ai_feedback.view', 'AI', 'View AI feedback'),
    ('support.view', 'Support', 'View support tickets'),
    ('support.manage', 'Support', 'Assign, reply to, and resolve support tickets'),
    ('audit_logs.view', 'Audit', 'View audit logs'),
    ('system.view', 'System', 'View system'),
    ('system.health', 'System', 'View system health'),
    ('system.settings', 'System', 'Manage system settings'),
    ('subscriptions.view', 'Subscriptions', 'View subscriptions'),
    ('subscriptions.create', 'Subscriptions', 'Create subscriptions'),
    ('subscriptions.update', 'Subscriptions', 'Update subscriptions'),
    ('subscriptions.cancel', 'Subscriptions', 'Cancel subscriptions'),
    ('subscription_plans.view', 'Subscriptions', 'View subscription plans'),
    ('subscription_plans.create', 'Subscriptions', 'Create subscription plans'),
    ('subscription_plans.update', 'Subscriptions', 'Update subscription plans'),
    ('subscription_plans.delete', 'Subscriptions', 'Delete subscription plans'),
]

DEFAULT_ROLES = {
    'super_admin': {
        'name': 'Super Admin',
        'description': 'Full platform access.',
        'is_system': True,
        'permissions': 'all',
    },
    'admin': {
        'name': 'Admin',
        'description': 'General read-focused platform admin.',
        'is_system': True,
        'permissions': [
            'dashboard.view',
            'users.view',
            'students.view',
            'sources.view',
            'collections.view',
            'study_plans.view',
            'quizzes.view',
            'character_interactions.view',
            'analytics.view',
            'ai_jobs.view',
            'ai_feedback.view',
        ],
    },
    'content_manager': {
        'name': 'Content Manager',
        'description': 'Manage academic content and quiz moderation.',
        'is_system': True,
        'permissions': [
            'dashboard.view',
            'education_stages.view',
            'education_stages.create',
            'education_stages.update',
            'education_stages.delete',
            'subjects.view',
            'subjects.create',
            'subjects.update',
            'subjects.delete',
            'quizzes.view',
            'quizzes.moderate',
        ],
    },
    'support': {
        'name': 'Support',
        'description': 'Support users and review learning data.',
        'is_system': True,
        'permissions': [
            'dashboard.view',
            'users.view',
            'users.update',
            'students.view',
            'sources.view',
            'collections.view',
            'support.view',
            'support.manage',
        ],
    },
    'finance': {
        'name': 'Finance',
        'description': 'Subscription readiness role.',
        'is_system': True,
        'permissions': [
            'dashboard.view',
            'users.view',
            'subscriptions.view',
            'subscriptions.update',
            'subscription_plans.view',
        ],
    },
    'analyst': {
        'name': 'Analyst',
        'description': 'Read-only analytics role.',
        'is_system': True,
        'permissions': [
            'dashboard.view',
            'analytics.view',
            'users.view',
            'sources.view',
            'collections.view',
            'quizzes.view',
        ],
    },
}

SECTION_PERMISSIONS = {
    'dashboard': 'dashboard.view',
    'users': 'users.view',
    'admins': 'admins.view',
    'roles': 'roles.view',
    'students': 'students.view',
    'sources': 'sources.view',
    'collections': 'collections.view',
    'study': 'study_plans.view',
    'quizzes': 'quizzes.view',
    'character_interactions': 'character_interactions.view',
    'analytics': 'analytics.view',
    'ai': 'ai_jobs.view',
    'support': 'support.view',
    'subscriptions': 'subscriptions.view',
    'subscription_plans': 'subscription_plans.view',
    'audit_logs': 'audit_logs.view',
    'system': 'system.view',
}


def seed_default_rbac():
    with transaction.atomic():
        permission_objects = {}
        for code, category, name in DEFAULT_PERMISSIONS:
            permission, _ = AdminPermission.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'category': category,
                    'is_active': True,
                },
            )
            permission_objects[code] = permission

        all_permissions = list(permission_objects.values())
        role_objects = {}
        for code, payload in DEFAULT_ROLES.items():
            role, _ = AdminRole.objects.update_or_create(
                code=code,
                defaults={
                    'name': payload['name'],
                    'description': payload['description'],
                    'is_system': payload['is_system'],
                    'is_active': True,
                },
            )
            permissions = all_permissions
            if payload['permissions'] != 'all':
                permissions = [
                    permission_objects[permission_code]
                    for permission_code in payload['permissions']
                ]
            role.permissions.set(permissions)
            role_objects[code] = role
    return permission_objects, role_objects


def get_user_admin_permissions(user):
    if not user or not user.is_authenticated or not user.is_active:
        return set()
    if user.is_superuser:
        return set(AdminPermission.objects.filter(is_active=True).values_list('code', flat=True))

    active_roles = AdminRole.objects.filter(
        user_roles__user=user,
        user_roles__is_active=True,
        is_active=True,
    )
    if active_roles.filter(code=SUPER_ADMIN_ROLE).exists():
        return set(AdminPermission.objects.filter(is_active=True).values_list('code', flat=True))
    return set(
        AdminPermission.objects.filter(
            roles__in=active_roles,
            is_active=True,
        )
        .values_list('code', flat=True)
        .distinct()
    )


def user_has_admin_permission(user, code):
    return code in get_user_admin_permissions(user)


def user_has_any_admin_permission(user, codes):
    permissions = get_user_admin_permissions(user)
    return any(code in permissions for code in codes)


def user_is_admin_dashboard_user(user):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    if getattr(user, 'role', None) == get_user_model().Roles.STUDENT:
        return False
    return AdminUserRole.objects.filter(
        user=user,
        is_active=True,
        role__is_active=True,
    ).exists() or user.is_staff


def is_super_admin_user(user):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or AdminUserRole.objects.filter(
        user=user,
        is_active=True,
        role__is_active=True,
        role__code=SUPER_ADMIN_ROLE,
    ).exists()


def assign_roles_to_user(user, roles, assigned_by=None):
    role_ids = [role.id for role in roles]
    AdminUserRole.objects.filter(user=user).exclude(role_id__in=role_ids).update(is_active=False)
    assignments = []
    for role in roles:
        assignment, _ = AdminUserRole.objects.update_or_create(
            user=user,
            role=role,
            defaults={'assigned_by': assigned_by, 'is_active': True},
        )
        assignments.append(assignment)
    return assignments


def count_super_admins(exclude_user=None):
    queryset = get_user_model().objects.filter(is_active=True).filter(
        models.Q(is_superuser=True)
        | models.Q(admin_user_roles__is_active=True, admin_user_roles__role__code=SUPER_ADMIN_ROLE)
    )
    if exclude_user is not None:
        queryset = queryset.exclude(pk=exclude_user.pk)
    return queryset.distinct().count()


def get_client_ip(request):
    if request is None:
        return None
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_admin_action(actor, action, target=None, metadata=None, request=None):
    try:
        target_type = ''
        target_id = None
        if target is not None:
            target_type = target.__class__.__name__
            target_id = str(getattr(target, 'pk', '') or '')
        return AuditLog.objects.create(
            actor=actor if getattr(actor, 'is_authenticated', False) else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
            ip_address=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '') if request else '',
        )
    except Exception:
        logger.exception('Failed to write admin audit log.')
        return None
