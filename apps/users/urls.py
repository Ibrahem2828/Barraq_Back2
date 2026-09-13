from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from .views import (
    AuthRootView,
    ChangePasswordView,
    LoginView,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
    ResendEmailOTPView,
    UserMeView,
    VerifyEmailOTPView,
)

urlpatterns = [
    path('auth/', AuthRootView.as_view(), name='auth-root'),
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/login/', LoginView.as_view(), name='login'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('auth/verify/', TokenVerifyView.as_view(), name='token-verify'),
    # Distinct name/path from `auth/verify/` above (SimpleJWT's unrelated
    # token-verify endpoint) -- this is the registration email-OTP check.
    path('auth/verify-email/', VerifyEmailOTPView.as_view(), name='verify-email'),
    path('auth/resend-otp/', ResendEmailOTPView.as_view(), name='resend-otp'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='change-password'),
    path('auth/password-reset/', PasswordResetRequestView.as_view(), name='password-reset'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('users/me/', UserMeView.as_view(), name='user-me'),
]
