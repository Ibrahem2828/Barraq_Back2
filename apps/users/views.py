from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view, inline_serializer
from rest_framework import generics, permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import (
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    LogoutSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    ResendEmailOTPSerializer,
    UserSerializer,
    VerifyEmailOTPSerializer,
)
from .services import delete_user_account, issue_email_otp, resend_cooldown_remaining_seconds, verify_email_otp
from .tasks import send_email_otp, send_password_reset_email

User = get_user_model()

MESSAGE_RESPONSE = inline_serializer(name="MessageResponse", fields={"message": serializers.CharField()})


@extend_schema(tags=["Auth"], responses=OpenApiTypes.OBJECT)
class AuthRootView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        base = request.build_absolute_uri("/").rstrip("/")
        return Response(
            {
                "register": f"{base}/api/v1/auth/register/",
                "login": f"{base}/api/v1/auth/login/",
                "refresh": f"{base}/api/v1/auth/refresh/",
                "verify": f"{base}/api/v1/auth/verify/",
                "verify_email": f"{base}/api/v1/auth/verify-email/",
                "resend_otp": f"{base}/api/v1/auth/resend-otp/",
                "logout": f"{base}/api/v1/auth/logout/",
                "password_reset": f"{base}/api/v1/auth/password-reset/",
            }
        )


@extend_schema(tags=["Auth"])
class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_scope = "register"


@extend_schema(tags=["Auth"])
class LoginView(TokenObtainPairView):
    throttle_scope = "login"
    serializer_class = CustomTokenObtainPairSerializer
    # simplejwt's TokenViewBase stub types both of these as the empty tuple
    # `()`; overriding them to open up login itself is the standard pattern.
    permission_classes = [permissions.AllowAny]  # type: ignore[assignment]
    authentication_classes: list = []  # type: ignore[assignment]


@extend_schema_view(
    delete=extend_schema(
        tags=["Users"],
        request=None,
        responses={200: MESSAGE_RESPONSE},
        description="Permanently deletes the caller's own account (self-service, GDPR/app-store "
        "style account deletion). Soft-deletes and anonymizes the account (email/name/phone "
        "are cleared, the row is kept only for legal/audit purposes tied to unrelated records), "
        "revokes every outstanding refresh token, and immediately prevents any further "
        "authentication as this user -- including with an access token issued before the call "
        "that has not yet expired. Idempotent: calling it again (were that ever possible -- it "
        "isn't, once the account can no longer authenticate) is a safe no-op.",
    ),
)
@extend_schema(tags=["Users"])
class UserMeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_object(self):
        return self.request.user

    def delete(self, request, *args, **kwargs):
        delete_user_account(request.user)
        return Response({"message": "Your account has been deleted."})


@extend_schema(tags=["Auth"], request=LogoutSerializer, responses={204: None, 400: OpenApiTypes.OBJECT})
class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError:
            return Response({"detail": "Refresh token is invalid."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Auth"], request=ChangePasswordSerializer, responses={200: MESSAGE_RESPONSE})
class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password", "updated_at"])
        return Response({"message": "Password changed successfully."})


@extend_schema(tags=["Auth"], request=PasswordResetRequestSerializer, responses={200: MESSAGE_RESPONSE})
class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=serializer.validated_data["email"], is_active=True).first()
        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = f"{settings.FRONTEND_PASSWORD_RESET_URL}?uid={uid}&token={token}"
            send_password_reset_email.delay(user.email, reset_url)
        return Response({"message": "If the account exists, reset instructions have been sent."})


@extend_schema(
    tags=["Auth"],
    request=VerifyEmailOTPSerializer,
    responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
    description="Verifies a registration email-OTP code and, on success, logs the user in "
    "(same response shape as /auth/login/): {access, refresh, user}.",
)
class VerifyEmailOTPView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_otp_verify"

    def post(self, request):
        serializer = VerifyEmailOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        code = serializer.validated_data["code"]

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            raise serializers.ValidationError({"error_code": "otp_invalid"})

        result = verify_email_otp(user, code)
        if result not in {"ok", "already_verified"}:
            raise serializers.ValidationError({"error_code": f"otp_{result}"})

        token = CustomTokenObtainPairSerializer.get_token(user)
        return Response(
            {
                "refresh": str(token),
                "access": str(token.access_token),
                "user": UserSerializer(user).data,
            }
        )


@extend_schema(
    tags=["Auth"],
    request=ResendEmailOTPSerializer,
    responses={200: MESSAGE_RESPONSE, 400: OpenApiTypes.OBJECT},
)
class ResendEmailOTPView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_otp_resend"

    def post(self, request):
        serializer = ResendEmailOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        # Deliberately silent for a nonexistent/already-verified account (same
        # non-disclosure pattern as PasswordResetRequestView below) -- only a
        # genuinely pending registration can ever see the cooldown error.
        user = User.objects.filter(email__iexact=email, is_active=True, is_verified=False).first()
        if user is not None:
            remaining = resend_cooldown_remaining_seconds(user)
            if remaining > 0:
                raise serializers.ValidationError(
                    {"error_code": "otp_resend_cooldown", "retry_after_seconds": remaining}
                )
            code = issue_email_otp(user)
            send_email_otp.delay(user.email, code)
        return Response({"message": "If the account exists and is unverified, a new code has been sent."})


@extend_schema(
    tags=["Auth"],
    request=PasswordResetConfirmSerializer,
    responses={200: MESSAGE_RESPONSE, 400: OpenApiTypes.OBJECT},
)
class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user_id = force_str(urlsafe_base64_decode(serializer.validated_data["uid"]))
            user = User.objects.get(pk=user_id, is_active=True)
        except (ValueError, TypeError, OverflowError, User.DoesNotExist):
            return Response({"detail": "Invalid reset link."}, status=status.HTTP_400_BAD_REQUEST)
        if not default_token_generator.check_token(user, serializer.validated_data["token"]):
            return Response({"detail": "Invalid or expired reset token."}, status=status.HTTP_400_BAD_REQUEST)
        from django.contrib.auth.password_validation import validate_password

        try:
            validate_password(serializer.validated_data["new_password"], user)
        except DjangoValidationError as exc:
            return Response({"new_password": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        return Response({"message": "Password reset successfully."})
