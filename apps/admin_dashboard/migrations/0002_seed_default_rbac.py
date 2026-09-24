"""Seed every default admin permission and role, additively.

Production's RBAC was seeded once by `bootstrap_baraq`, before the school
(organization), class, invitation and join-request permissions existed, and
that command is not part of a deploy. Fifteen permissions and the
organization_manager / class_supervisor roles were therefore missing, and
every school endpoint answered 403 -- even to super admins, whose
permission set is "every active permission in the database".

Frozen copy of apps.admin_dashboard.services.DEFAULT_PERMISSIONS /
DEFAULT_ROLES as of 2026-09-25. Additive only: it creates what is missing and
adds a role's default permissions, never renames, deactivates or removes
anything an administrator changed.
"""

from django.db import migrations

PERMISSIONS = [('dashboard.view', 'Dashboard', 'View dashboard'),
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
 ('subscription_plans.delete', 'Subscriptions', 'Delete subscription plans')]

ROLES = {'super_admin': {'name': 'Super Admin',
                 'description': 'Full platform access.',
                 'permissions': 'all'},
 'admin': {'name': 'Admin',
           'description': 'General read-focused platform admin.',
           'permissions': ['dashboard.view',
                           'users.view',
                           'students.view',
                           'sources.view',
                           'collections.view',
                           'study_plans.view',
                           'quizzes.view',
                           'character_interactions.view',
                           'analytics.view',
                           'ai_jobs.view',
                           'ai_feedback.view']},
 'organization_manager': {'name': 'Organization Manager',
                          'description': 'Runs one school or institute. Its reach comes from the '
                                         'scope attached to the assignment, not from this name.',
                          'permissions': ['dashboard.view',
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
                                          'education_stages.view']},
 'class_supervisor': {'name': 'Class Supervisor',
                      'description': 'Runs the classes its assignment is scoped to. Holds no '
                                     'organization-wide grant.',
                      'permissions': ['dashboard.view',
                                      'classes.view',
                                      'class_members.view',
                                      'class_members.manage',
                                      'join_requests.view',
                                      'join_requests.manage',
                                      'subjects.view',
                                      'education_stages.view']},
 'content_manager': {'name': 'Content Manager',
                     'description': 'Manage academic content and quiz moderation.',
                     'permissions': ['dashboard.view',
                                     'education_stages.view',
                                     'education_stages.create',
                                     'education_stages.update',
                                     'education_stages.delete',
                                     'subjects.view',
                                     'subjects.create',
                                     'subjects.update',
                                     'subjects.delete',
                                     'quizzes.view',
                                     'quizzes.moderate']},
 'support': {'name': 'Support',
             'description': 'Support users and review learning data.',
             'permissions': ['dashboard.view',
                             'users.view',
                             'users.update',
                             'students.view',
                             'sources.view',
                             'collections.view',
                             'support.view',
                             'support.manage']},
 'finance': {'name': 'Finance',
             'description': 'Subscription readiness role.',
             'permissions': ['dashboard.view',
                             'users.view',
                             'subscriptions.view',
                             'subscriptions.update',
                             'subscription_plans.view']},
 'analyst': {'name': 'Analyst',
             'description': 'Read-only analytics role.',
             'permissions': ['dashboard.view',
                             'analytics.view',
                             'users.view',
                             'sources.view',
                             'collections.view',
                             'quizzes.view']}}


def seed_rbac(apps, schema_editor):
    AdminPermission = apps.get_model("admin_dashboard", "AdminPermission")
    AdminRole = apps.get_model("admin_dashboard", "AdminRole")
    by_code = {}
    for code, category, name in PERMISSIONS:
        permission, _ = AdminPermission.objects.get_or_create(
            code=code, defaults={"name": name, "category": category, "is_active": True}
        )
        by_code[code] = permission
    for code, spec in ROLES.items():
        role, _ = AdminRole.objects.get_or_create(
            code=code,
            defaults={
                "name": spec["name"],
                "description": spec["description"],
                "is_system": True,
                "is_active": True,
            },
        )
        wanted = PERMISSIONS if spec["permissions"] == "all" else spec["permissions"]
        codes = [item[0] for item in wanted] if spec["permissions"] == "all" else wanted
        role.permissions.add(*[by_code[item] for item in codes])


class Migration(migrations.Migration):
    dependencies = [
        ("admin_dashboard", "0001_initial"),
    ]

    operations = [
        # Nothing to reverse safely: the rows may predate this migration.
        migrations.RunPython(seed_rbac, migrations.RunPython.noop),
    ]
