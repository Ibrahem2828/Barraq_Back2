import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("subjects", "0002_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Project",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("public_id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ("title", models.CharField(max_length=255)),
                ("goal", models.TextField(blank=True)),
                ("education_context", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("active", "Active"), ("archived", "Archived")], db_index=True, default="active", max_length=20)),
                ("color", models.CharField(blank=True, max_length=40)),
                ("icon", models.CharField(blank=True, max_length=80)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="projects", to=settings.AUTH_USER_MODEL)),
                ("subject", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="projects", to="subjects.subject")),
            ],
            options={"ordering": ("-updated_at", "-created_at")},
        ),
        migrations.CreateModel(
            name="ProjectActivity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event_type", models.CharField(db_index=True, max_length=80)),
                ("request_id", models.CharField(blank=True, db_index=True, max_length=128)),
                ("artifact_type", models.CharField(blank=True, max_length=80)),
                ("artifact_id", models.CharField(blank=True, max_length=80)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="project_activities", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="activities", to="projects.project")),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddConstraint(model_name="project", constraint=models.CheckConstraint(condition=~models.Q(("title", "")), name="project_title_not_blank")),
        migrations.AddIndex(model_name="project", index=models.Index(fields=["owner", "status", "-updated_at"], name="project_owner_status_idx")),
        migrations.AddIndex(model_name="project", index=models.Index(fields=["owner", "subject", "-created_at"], name="project_owner_subject_idx")),
        migrations.AddIndex(model_name="projectactivity", index=models.Index(fields=["project", "-created_at"], name="project_activity_timeline_idx")),
    ]
