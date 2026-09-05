from django.contrib.auth.base_user import BaseUserManager
from django.core.exceptions import FieldDoesNotExist

from apps.common.models import SoftDeleteQuerySet


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_queryset(self):
        # ``use_in_migrations`` means this exact class is also used against
        # historical model states from migrations older than the one that
        # added ``is_deleted`` (e.g. 0002_email_case_insensitive predates it).
        # Filtering unconditionally would raise FieldError on those replays.
        try:
            self.model._meta.get_field('is_deleted')
        except FieldDoesNotExist:
            return super().get_queryset()
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The email address must be provided.')

        email = self.normalize_email(email).strip().lower()
        extra_fields.setdefault('is_active', True)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault('role', self.model.Roles.SUPER_ADMIN)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, password, **extra_fields)
