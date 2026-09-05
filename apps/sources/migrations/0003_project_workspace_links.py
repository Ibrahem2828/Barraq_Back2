import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0001_initial"),
        ("sources", "0002_alter_studentsourceinteraction_source_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentsource",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sources", to="projects.project"),
        ),
        migrations.AddField(
            model_name="studentsourcecollection",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="source_collections", to="projects.project"),
        ),
        migrations.AddIndex(
            model_name="studentsource",
            index=models.Index(fields=["project", "-created_at"], name="source_project_timeline_idx"),
        ),
        migrations.AddIndex(
            model_name="studentsourcecollection",
            index=models.Index(fields=["project", "status"], name="source_collection_project_idx"),
        ),
    ]
