import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0001_initial"),
        ("sources", "0003_project_workspace_links"),
        ("ai_integration", "0002_canonical_task_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="aijob",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ai_jobs", to="projects.project"),
        ),
        migrations.AddField(model_name="aijob", name="contract_version", field=models.CharField(db_index=True, default="2.0", max_length=20)),
        migrations.AddField(model_name="aijob", name="request_id", field=models.CharField(blank=True, db_index=True, max_length=128)),
        migrations.AddField(model_name="aijob", name="quality_metrics", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="aijob", name="security_flags", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="aijob", name="output_schema_version", field=models.CharField(blank=True, max_length=20)),
        migrations.AlterField(
            model_name="aijob",
            name="status",
            field=models.CharField(choices=[("created", "Created"), ("queued", "Queued"), ("submitted", "Submitted to AI service"), ("processing", "Processing"), ("validating", "Validating"), ("output_ready", "Output ready"), ("materializing", "Materializing"), ("completed", "Completed"), ("failed", "Failed"), ("canceled", "Canceled")], db_index=True, default="created", max_length=20),
        ),
        migrations.AddConstraint(
            model_name="aijob",
            constraint=models.CheckConstraint(condition=(~Q(("status", "completed"))) | (Q(("result_type__gt", "")) & Q(("result_id__gt", ""))), name="ai_job_completed_has_materialized_result"),
        ),
        migrations.AddIndex(model_name="aijob", index=models.Index(fields=["project", "status", "-created_at"], name="ai_job_project_status_idx")),
    ]
