from django.db import models

from apps.common.models import BaseModel


class WaitlistEntry(BaseModel):
    class Locale(models.TextChoices):
        AR = "ar", "Arabic"
        EN = "en", "English"

    class Source(models.TextChoices):
        WEBSITE = "website", "Marketing website"

    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=150, blank=True)
    locale = models.CharField(max_length=8, choices=Locale.choices, default=Locale.AR)
    source = models.CharField(max_length=30, choices=Source.choices, default=Source.WEBSITE)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)
