from rest_framework.permissions import SAFE_METHODS, BasePermission

STAFF_ROLES = {'admin', 'support', 'super_admin'}


class IsStaffRole(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, 'role', None) in STAFF_ROLES
        )


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, 'role', None) == 'super_admin'
        )


class ReadOnlyOrStaffRole(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return IsStaffRole().has_permission(request, view)
