import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0001_initial"),
        ("study_plans", "0002_studyplan_study_plan_user_status_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="studyplan",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="study_plans", to="projects.project"),
        ),
        migrations.AddIndex(
            model_name="studyplan",
            index=models.Index(fields=["project", "status", "-created_at"], name="study_plan_project_status_idx"),
        ),
    ]
