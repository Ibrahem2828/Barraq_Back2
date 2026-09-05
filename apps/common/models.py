from django.db import models
from django.utils import timezone


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(is_deleted=False)

    def dead(self):
        return self.filter(is_deleted=True)

    def delete(self):
        """Bulk soft-delete: mark matched rows deleted instead of removing them."""
        return self.update(is_deleted=True, deleted_at=timezone.now())

    def hard_delete(self):
        """Bulk permanent delete, bypassing the soft-delete override."""
        return super().delete()


class SoftDeleteManager(models.Manager):
    """Excludes soft-deleted rows. Use ``all_objects`` to include them."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)


class SoftDeleteModel(BaseModel):
    """Abstract base for models where destructive user actions (account,
    subscription, quiz attempt) must be recoverable instead of cascading a
    real DELETE through dependent rows."""

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager.from_queryset(SoftDeleteQuerySet)()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(using=using, update_fields=['is_deleted', 'deleted_at', 'updated_at'])

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)
