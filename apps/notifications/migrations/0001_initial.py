import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.CharField(choices=[("system", "System"), ("study", "Study"), ("quiz", "Quiz"), ("plan", "Plan"), ("ai", "AI"), ("subscription", "Subscription"), ("support", "Support")], db_index=True, default="system", max_length=30)),
                ("title", models.CharField(max_length=255)),
                ("body", models.TextField()),
                ("data", models.JSONField(blank=True, default=dict)),
                ("action_url", models.CharField(blank=True, max_length=500)),
                ("read_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddIndex(model_name="notification", index=models.Index(fields=["user", "read_at", "-created_at"], name="notif_user_read_idx")),
    ]
