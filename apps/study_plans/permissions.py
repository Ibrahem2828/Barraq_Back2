from rest_framework.permissions import BasePermission


class IsStudyPlanOwner(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'user_id'):
            return obj.user_id == request.user.id
        if hasattr(obj, 'plan_id'):
            return obj.plan.user_id == request.user.id
        return False
