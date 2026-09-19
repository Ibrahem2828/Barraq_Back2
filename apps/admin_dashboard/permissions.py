from rest_framework import permissions

from .services import (
    user_has_admin_permission,
    user_is_admin_dashboard_user,
)


class IsAdminDashboardUser(permissions.BasePermission):
    message = 'Admin dashboard access is not allowed.'

    def has_permission(self, request, view):
        return user_is_admin_dashboard_user(request.user)


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
        return user_has_admin_permission(request.user, required_permission)


def require_admin_permission(code):
    class RequiredAdminPermission(HasAdminPermission):
        def has_permission(self, request, view):
            return user_has_admin_permission(request.user, code)

    return RequiredAdminPermission
