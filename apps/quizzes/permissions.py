from rest_framework.permissions import BasePermission


class IsQuizOwner(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if hasattr(obj, 'user_id'):
            return obj.user_id == user.id
        if hasattr(obj, 'quiz_id'):
            return obj.quiz.user_id == user.id
        if hasattr(obj, 'attempt_id'):
            return obj.attempt.user_id == user.id
        return False


class CanAccessQuestionBankItem(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        return bool(obj.is_public or obj.created_by_id == request.user.id)
