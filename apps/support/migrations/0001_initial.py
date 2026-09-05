import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="SupportTicket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("subject", models.CharField(max_length=255)),
                ("category", models.CharField(choices=[("technical", "Technical"), ("account", "Account"), ("billing", "Billing"), ("content", "Content"), ("ai_result", "AI result"), ("other", "Other")], db_index=True, default="other", max_length=30)),
                ("priority", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("urgent", "Urgent")], db_index=True, default="medium", max_length=20)),
                ("status", models.CharField(choices=[("open", "Open"), ("in_progress", "In progress"), ("waiting_user", "Waiting for user"), ("resolved", "Resolved"), ("closed", "Closed")], db_index=True, default="open", max_length=30)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("assigned_to", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assigned_support_tickets", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_tickets", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ("-updated_at",)},
        ),
        migrations.CreateModel(
            name="SupportMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("body", models.TextField()),
                ("is_internal", models.BooleanField(default=False)),
                ("sender", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_messages", to=settings.AUTH_USER_MODEL)),
                ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="support.supportticket")),
            ], options={"ordering": ("created_at",)},
        ),
        migrations.AddIndex(model_name="supportticket", index=models.Index(fields=["status", "priority", "-created_at"], name="support_status_priority_idx")),
        migrations.AddIndex(model_name="supportticket", index=models.Index(fields=["user", "-created_at"], name="support_user_date_idx")),
    ]
