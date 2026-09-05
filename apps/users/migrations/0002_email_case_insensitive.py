from django.db import migrations, models
from django.db.models.functions import Lower


def normalize_existing_emails(apps, schema_editor):
    User = apps.get_model("users", "User")
    seen = set()
    for user in User.objects.all().only("id", "email").iterator():
        normalized = (user.email or "").strip().lower()
        if normalized in seen:
            raise RuntimeError(f"Duplicate case-insensitive email detected: {normalized}")
        seen.add(normalized)
        if normalized != user.email:
            user.email = normalized
            user.save(update_fields=["email"])


class Migration(migrations.Migration):
    dependencies = [("users", "0001_initial")]
    operations = [
        migrations.RunPython(normalize_existing_emails, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(Lower("email"), name="unique_user_email_case_insensitive"),
        ),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(fields=["role", "is_active"], name="user_role_active_idx"),
        ),
    ]
