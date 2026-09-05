from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.common.models import SoftDeleteModel

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
        self.email = self.__class__.objects.normalize_email(self.email).strip().lower()
        if self.is_superuser:
            self.role = self.Roles.SUPER_ADMIN
        self.is_staff = (
            self.role in {self.Roles.ADMIN, self.Roles.SUPPORT, self.Roles.SUPER_ADMIN}
            or self.is_superuser
        )
        super().save(*args, **kwargs)
