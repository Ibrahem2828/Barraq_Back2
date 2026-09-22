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
    ('organizations.view', 'Organizations', 'View organizations'),
    ('organizations.create', 'Organizations', 'Create organizations'),
    ('organizations.update', 'Organizations', 'Update organizations'),
    ('organizations.archive', 'Organizations', 'Archive organizations'),
    ('organizations.manage_members', 'Organizations', 'Manage organization members'),
    ('classes.view', 'Classes', 'View classes'),
    ('classes.create', 'Classes', 'Create classes'),
    ('classes.update', 'Classes', 'Update classes'),
    ('classes.archive', 'Classes', 'Archive classes'),
    ('class_members.view', 'Classes', 'View class members'),
    ('class_members.manage', 'Classes', 'Manage class members'),
    ('invitations.view', 'Classes', 'View class invitations'),
    ('invitations.manage', 'Classes', 'Create and revoke class invitations'),
    ('join_requests.view', 'Classes', 'View join requests'),
    ('join_requests.manage', 'Classes', 'Approve or reject join requests'),
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
    'organization_manager': {
        'name': 'Organization Manager',
        'description': 'Runs one school or institute. Its reach comes from the '
                       'scope attached to the assignment, not from this name.',
        'is_system': True,
        'permissions': [
            'dashboard.view',
            'organizations.view',
            'organizations.manage_members',
            'classes.view',
            'classes.create',
            'classes.update',
            'classes.archive',
            'class_members.view',
            'class_members.manage',
            'invitations.view',
            'invitations.manage',
            'join_requests.view',
            'join_requests.manage',
            'users.view',
            'subjects.view',
            'education_stages.view',
        ],
    },
    'class_supervisor': {
        'name': 'Class Supervisor',
        'description': 'Runs the classes its assignment is scoped to. Holds no '
                       'organization-wide grant.',
        'is_system': True,
        'permissions': [
            'dashboard.view',
            'classes.view',
            'class_members.view',
            'class_members.manage',
            'join_requests.view',
            'join_requests.manage',
            'subjects.view',
            'education_stages.view',
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
    'organizations': 'organizations.view',
    'classes': 'classes.view',
    'invitations': 'invitations.view',
    'join_requests': 'join_requests.view',
    # `subjects.*` permissions existed with no section entry, so the
    # dashboard's education nav resolved to undefined and was hidden from
    # every non-superuser admin.
    'subjects': 'subjects.view',
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
    if is_super_admin_user(user):
        return set(AdminPermission.objects.filter(is_active=True).values_list('code', flat=True))

    active_roles = AdminRole.objects.filter(
        user_roles__user=user,
        user_roles__is_active=True,
        is_active=True,
    )
    # A malformed legacy super_admin assignment without its required GLOBAL
    # scope must not retain the role's many-to-many permission set.  A valid
    # platform administrator was handled above; everything else fails closed.
    active_roles = active_roles.exclude(code=SUPER_ADMIN_ROLE)
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
    # `User.role` is an account classification, not an authorization grant or
    # denial.  In particular, a legacy account may have been created as a
    # student and later granted an explicit administrative assignment.  Using
    # the legacy string as a deny-list meant that valid RBAC grants were never
    # evaluated and every dashboard endpoint returned the same 403.
    #
    # Deliberately NOT `or user.is_staff`: User.save() historically marks
    # admin-ish account classifications as staff. Access follows an explicit,
    # revocable assignment with an explicit scope.
    return AdminUserRole.objects.filter(
        user=user,
        is_active=True,
        role__is_active=True,
        scopes__isnull=False,
    ).exists()


#: The applications an authenticated account may enter. Backend-authoritative:
#: a client renders what this says, it does not decide it. Kept as plain
#: identifiers with no resource/organization ids in them, so the later
#: organization phase can add *scope* ("on whose data") without having to
#: redesign what an app-level grant means.
APP_STUDENT_WEB = 'student_web'
APP_DASHBOARD = 'dashboard'


def get_allowed_apps(user):
    """Which front-end applications this account may enter.

    Student Web is available to every active account -- that is today's
    behaviour (no student-side endpoint checks role) and narrowing it would
    lock staff out of their own study data. Dashboard follows the same
    explicit role assignment the dashboard gate enforces, so this list can
    never promise more than the API itself allows.
    """

    if not user or not getattr(user, 'is_authenticated', False) or not user.is_active:
        return []
    apps = [APP_STUDENT_WEB]
    if user_is_admin_dashboard_user(user):
        apps.append(APP_DASHBOARD)
    return apps


def is_super_admin_user(user):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or AdminUserRole.objects.filter(
        user=user,
        is_active=True,
        role__is_active=True,
        role__code=SUPER_ADMIN_ROLE,
        scopes__scope_type="global",
    ).exists()


_SCOPES_UNSET = object()


def assign_roles_to_user(user, roles, assigned_by=None, scopes=_SCOPES_UNSET):
    """Assign roles, and the data scope each one applies to.

    A caller must state scopes explicitly.  The historical implicit GLOBAL
    default was safe only during the one-time data migration; retaining it in
    runtime code lets a future endpoint accidentally mint platform access.
    Pass ``[{"scope_type": "global"}]`` deliberately for a platform grant,
    or ``[]`` when creating an intentionally unscoped (and therefore
    ineffective) assignment for a controlled remediation workflow.

    Scope rows are replaced rather than merged: re-assigning a role is how an
    operator narrows or moves someone's reach, and merging would make
    revoking an organization impossible through this path.
    """
    from apps.organizations.models import AdminRoleScope

    if scopes is _SCOPES_UNSET:
        raise ValueError("Explicit role scopes are required.")

    roles = list(roles)
    normalized_scopes = list(scopes)
    if any(role.code == SUPER_ADMIN_ROLE for role in roles) and normalized_scopes != [
        {"scope_type": AdminRoleScope.ScopeType.GLOBAL}
    ]:
        raise ValueError("The super_admin role requires exactly one explicit GLOBAL scope.")

    # Replacement is a security boundary: a malformed request must not first
    # deactivate the old assignments and then fail halfway through creating
    # its replacement.  Concurrent assignment writers likewise see one
    # complete grant set, never a transient empty or over-broad one.
    with transaction.atomic():
        role_ids = [role.id for role in roles]
        AdminUserRole.objects.filter(user=user).exclude(role_id__in=role_ids).update(is_active=False)
        assignments = []
        for role in roles:
            assignment, _ = AdminUserRole.objects.update_or_create(
                user=user,
                role=role,
                defaults={'assigned_by': assigned_by, 'is_active': True},
            )
            AdminRoleScope.objects.filter(admin_user_role=assignment).delete()
            for scope in normalized_scopes:
                AdminRoleScope.objects.create(
                    admin_user_role=assignment,
                    scope_type=scope.get('scope_type', AdminRoleScope.ScopeType.GLOBAL),
                    organization=scope.get('organization'),
                    classroom=scope.get('classroom'),
                    granted_by=assigned_by,
                )
            assignments.append(assignment)
    return assignments


def count_super_admins(exclude_user=None):
    queryset = get_user_model().objects.filter(is_active=True).filter(
        models.Q(is_superuser=True)
        | models.Q(
            admin_user_roles__is_active=True,
            admin_user_roles__role__is_active=True,
            admin_user_roles__role__code=SUPER_ADMIN_ROLE,
            admin_user_roles__scopes__scope_type="global",
        )
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
