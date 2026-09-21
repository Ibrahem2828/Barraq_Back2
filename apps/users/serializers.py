from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .identity import normalize_email
from .models import phone_number_validator

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    # Backend-authoritative application access, so a client renders what it is
    # allowed rather than inferring it from the `role` string. Additive: `role`
    # is unchanged for existing clients. Imported lazily inside the method --
    # apps.admin_dashboard already imports apps.users, so a module-level
    # import here would close the cycle.
    allowed_apps = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'full_name',
            'phone_number',
            'role',
            'allowed_apps',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'email',
            'role',
            'allowed_apps',
            'created_at',
            'updated_at',
        )

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_allowed_apps(self, obj):
        from apps.admin_dashboard.services import get_allowed_apps

        return get_allowed_apps(obj)


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=255)
    phone_number = serializers.CharField(
        max_length=20,
        required=False,
        allow_blank=True,
        validators=[phone_number_validator],
    )
    password = serializers.CharField(write_only=True, min_length=10)
    password_confirm = serializers.CharField(write_only=True)

    def validate_email(self, value):
        return normalize_email(value)

    def validate(self, attrs):
        from django.contrib.auth.password_validation import validate_password
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError(
                {'password_confirm': 'Passwords do not match.'}
            )
        try:
            validate_password(attrs['password'])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)}) from exc
        attrs.pop('password_confirm')
        return attrs


class RegisterResponseSerializer(serializers.Serializer):
    verification_required = serializers.BooleanField()
    email = serializers.EmailField()
    expires_in = serializers.IntegerField(min_value=0)
    resend_after_seconds = serializers.IntegerField(min_value=0)


class ResendEmailOTPResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    expires_in = serializers.IntegerField(min_value=0)
    resend_after_seconds = serializers.IntegerField(min_value=0)


class VerifiedEmailOTPResponseSerializer(serializers.Serializer):
    """Session payload returned only after a pending registration is verified."""

    refresh = serializers.CharField()
    access = serializers.CharField()
    user = UserSerializer()


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['email'] = user.email
        token['role'] = user.role
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        # `error_code` (not `code`) deliberately -- the shared error envelope
        # (apps/common/exceptions.py) already sets its own top-level `code`
        # from the HTTP status for every validation error, and a same-named
        # key here would silently overwrite it.
        if not self.user.is_verified:
            raise serializers.ValidationError({'error_code': 'email_not_verified'})
        data['user'] = UserSerializer(self.user).data
        return data


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=10)

    def validate_current_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Current password is incorrect.')
        return value

    def validate_new_password(self, value):
        from django.contrib.auth.password_validation import validate_password
        try:
            validate_password(value, self.context['request'].user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=10)


class VerifyEmailOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.RegexField(r'^\d{6}$')

    def validate_email(self, value):
        return normalize_email(value)


class ResendEmailOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return normalize_email(value)
