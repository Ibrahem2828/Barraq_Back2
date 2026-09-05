import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0001_initial"),
        ("quizzes", "0002_quizattempt_deleted_at_quizattempt_is_deleted_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="quiz",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="quizzes", to="projects.project"),
        ),
        migrations.AddIndex(
            model_name="quiz",
            index=models.Index(fields=["project", "status", "-created_at"], name="quiz_project_status_idx"),
        ),
    ]
