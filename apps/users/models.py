from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel, SoftDeleteModel

from .identity import normalize_email
from .managers import UserManager

phone_number_validator = RegexValidator(
    regex=r'^[0-9+\-\s()]{7,20}$',
    message='Phone number format is invalid.',
)


class User(SoftDeleteModel, AbstractBaseUser, PermissionsMixin):
    class Roles(models.TextChoices):
        STUDENT = 'student', _('Student')
        ADMIN = 'admin', _('Admin')
        SUPPORT = 'support', _('Support')
        SUPER_ADMIN = 'super_admin', _('Super Admin')

    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255)
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        validators=[phone_number_validator],
    )
    role = models.CharField(
        max_length=20,
        choices=Roles.choices,
        default=Roles.STUDENT,
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    # Default True so the migration backfills every existing account as
    # already verified. New registrations live in PendingRegistration and a
    # permanent User is created only after its OTP succeeds. False is retained
    # solely for legacy registrations created before that flow was introduced.
    is_verified = models.BooleanField(default=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']

    # UserManager must subclass BaseUserManager (required by Django auth for
    # create_user/normalize_email) while duck-typing SoftDeleteModel's own
    # filtering by hand -- see managers.py. Not a real type-hierarchy bug,
    # just something django-stubs' manager-override check can't express.
    objects = UserManager()  # type: ignore[assignment,misc]

    class Meta:
        ordering = ('-created_at',)
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        constraints = [models.UniqueConstraint(Lower('email'), name='unique_user_email_case_insensitive')]
        indexes = [models.Index(fields=['role', 'is_active'], name='user_role_active_idx')]

    def __str__(self):
        return self.full_name or self.email

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(' ')[0] if self.full_name else self.email

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        if self.is_superuser:
            self.role = self.Roles.SUPER_ADMIN
        self.is_staff = (
            self.role in {self.Roles.ADMIN, self.Roles.SUPPORT, self.Roles.SUPER_ADMIN}
            or self.is_superuser
        )
        super().save(*args, **kwargs)


class EmailOTP(BaseModel):
    """Compatibility OTP for accounts created by the legacy registration flow.

    New registrations never use this model: they use ``PendingRegistration``
    and create a permanent ``User`` only after OTP verification.  Keeping the
    table avoids stranding historic unverified rows during a non-destructive
    rollout.  It can be retired after the documented compatibility window.
    """

    user = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='email_otps',
    )
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'consumed_at', '-created_at'], name='email_otp_lookup_idx')]

    def __str__(self):
        return f"EmailOTP for {self.user_id} (consumed={bool(self.consumed_at)})"


class PendingRegistration(BaseModel):
    """Minimal, short-lived data needed to create a verified student account.

    Neither a raw password nor a raw OTP is ever persisted.  ``normalized_email``
    is the registration identity and is protected by both an exact and a
    case-insensitive database uniqueness constraint.
    """

    normalized_email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255)
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        validators=[phone_number_validator],
    )
    password_hash = models.CharField(max_length=128)
    otp_hash = models.CharField(max_length=128)
    otp_expires_at = models.DateTimeField()
    otp_attempt_count = models.PositiveSmallIntegerField(default=0)
    otp_send_count = models.PositiveSmallIntegerField(default=0)
    otp_send_window_started_at = models.DateTimeField()
    last_otp_sent_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower('normalized_email'),
                name='unique_pending_registration_email_case_insensitive',
            ),
        ]
        indexes = [
            models.Index(fields=['otp_expires_at'], name='pending_reg_expiry_idx'),
            models.Index(fields=['updated_at'], name='pending_reg_cleanup_idx'),
        ]

    def __str__(self):
        return f"Pending registration for {self.normalized_email}"
