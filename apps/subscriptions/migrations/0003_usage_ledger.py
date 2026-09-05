import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("ai_integration", "0003_project_state_and_contract_fields"),
        ("subscriptions", "0002_usersubscription_deleted_at_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="UsageLedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("task_type", models.CharField(blank=True, db_index=True, max_length=50)),
                ("operation", models.CharField(choices=[("reserve", "Reserve"), ("commit", "Commit"), ("refund", "Refund"), ("adjustment", "Adjustment")], db_index=True, max_length=20)),
                ("units", models.PositiveIntegerField(default=1)),
                ("idempotency_key", models.CharField(max_length=128)),
                ("policy_version", models.CharField(default="usage-v1", max_length=40)),
                ("actor_type", models.CharField(default="system", max_length=30)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="usage_ledger_entries", to="ai_integration.aijob")),
                ("subscription", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="usage_ledger_entries", to="subscriptions.usersubscription")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="usage_ledger_entries", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.AddConstraint(model_name="usageledgerentry", constraint=models.UniqueConstraint(fields=("user", "operation", "idempotency_key"), name="unique_usage_ledger_operation_key")),
        migrations.AddConstraint(model_name="usageledgerentry", constraint=models.CheckConstraint(condition=Q(("units__gt", 0)), name="usage_ledger_units_positive")),
        migrations.AddIndex(model_name="usageledgerentry", index=models.Index(fields=["user", "period_start", "-created_at"], name="usage_ledger_user_period_idx")),
        migrations.AddIndex(model_name="usageledgerentry", index=models.Index(fields=["job", "operation"], name="usage_ledger_job_operation_idx")),
    ]
