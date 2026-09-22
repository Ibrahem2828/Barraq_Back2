from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from .services import (
    user_has_admin_permission,
    user_is_admin_dashboard_user,
)


class IsAdminDashboardUser(permissions.BasePermission):
    message = 'Admin dashboard access is not allowed.'

    def has_permission(self, request, view):
        return user_is_admin_dashboard_user(request.user)


class AdminScopePermissionDenied(PermissionDenied):
    """An authenticated administrator lacks a grant for this tenant action."""

    default_detail = "You do not have scope to perform this admin action."

    def __init__(self):
        self.domain_code = "insufficient_scope"
        super().__init__(self.default_detail)


class HasAdminPermission(permissions.BasePermission):
    message = 'You do not have permission to perform this admin action.'

    def has_permission(self, request, view):
        required_permission = getattr(view, 'required_permission', None)
        if required_permission is None and hasattr(view, 'get_required_permission'):
            required_permission = view.get_required_permission()
        if required_permission is None:
            # Fail closed. A view reached through this class without declaring
            # a permission is a mistake, and defaulting to "allow" turns that
            # mistake into an open admin endpoint for anyone who clears the
            # dashboard gate. Views that intentionally need no permission
            # (AdminMeView, AdminApiRootView) use IsAdminDashboardUser alone
            # and never reach here.
            return False
        if not user_has_admin_permission(request.user, required_permission):
            return False

        # A tenant-owned view must receive both the action permission and an
        # active assignment scope that belongs to a role supplying that exact
        # permission.  Import lazily because scope.py depends on the services
        # imported above; doing this at module load time would create a cycle.
        scope_types = getattr(view, "required_scope_types", None)
        get_scope_types = getattr(view, "get_required_scope_types", None)
        if get_scope_types is not None:
            scope_types = get_scope_types()
        requires_scope = getattr(view, "requires_scope", scope_types is not None)
        if requires_scope:
            from apps.organizations.scope import has_scope

            if not has_scope(request.user, required_permission, scope_types):
                raise AdminScopePermissionDenied()
        return True


def require_admin_permission(code):
    class RequiredAdminPermission(HasAdminPermission):
        def has_permission(self, request, view):
            return user_has_admin_permission(request.user, code)

    return RequiredAdminPermission
