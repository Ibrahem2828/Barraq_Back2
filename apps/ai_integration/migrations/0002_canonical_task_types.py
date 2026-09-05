from django.db import migrations, models


LEGACY_TO_CANONICAL = {
    "kholasa_summary": "kholasa_generate_summary",
    "sada_transcription": "sada_transcribe_audio",
}


def migrate_task_types(apps, schema_editor):
    AIJob = apps.get_model("ai_integration", "AIJob")
    for legacy_value, canonical_value in LEGACY_TO_CANONICAL.items():
        AIJob.objects.filter(task_type=legacy_value).update(task_type=canonical_value)


def reverse_task_types(apps, schema_editor):
    AIJob = apps.get_model("ai_integration", "AIJob")
    for legacy_value, canonical_value in LEGACY_TO_CANONICAL.items():
        AIJob.objects.filter(task_type=canonical_value).update(task_type=legacy_value)


class Migration(migrations.Migration):
    dependencies = [
        ("ai_integration", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(migrate_task_types, reverse_task_types),
        migrations.AlterField(
            model_name="aijob",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("fahes_generate_quiz", "Generate quiz"),
                    ("khota_generate_plan", "Generate study plan"),
                    ("rasheed_recommendations", "Performance recommendations"),
                    ("kholasa_generate_summary", "Summarize source"),
                    ("sada_transcribe_audio", "Transcribe audio"),
                ],
                db_index=True,
                max_length=50,
            ),
        ),
    ]
