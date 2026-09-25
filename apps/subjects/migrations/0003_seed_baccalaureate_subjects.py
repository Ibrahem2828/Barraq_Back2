"""The Baccalaureate stage and its default subjects.

Choosing "بكالوريا" gives the learner one project per active subject of the
stage (apps.projects.stage_projects), so the stage needs its subjects. More
are added from the dashboard; this seeds the core set.

Only what is missing is created. "العلوم" is not added when the stage already
has a science subject under another name (production has "علم أحياء"), so no
learner gets two science projects.
"""

from django.db import migrations

STAGE = "بكالوريا"
SCIENCE_NAMES = {"العلوم", "علوم", "علم أحياء", "علم الأحياء"}
SUBJECTS = [
    "العلوم",
    "اللغة العربية",
    "الفيزياء",
    "الكيمياء",
    "التاريخ",
    "الجغرافيا",
    "الرياضيات - الجبر",
    "الرياضيات - الهندسة",
]


def seed(apps, schema_editor):
    EducationStage = apps.get_model("subjects", "EducationStage")
    Subject = apps.get_model("subjects", "Subject")
    stage, _ = EducationStage.objects.get_or_create(name=STAGE, defaults={"order": 1, "is_active": True})
    existing = set(Subject.objects.filter(education_stage=stage).values_list("name", flat=True))
    for name in SUBJECTS:
        if name in existing:
            continue
        if name == "العلوم" and existing & SCIENCE_NAMES:
            continue
        Subject.objects.create(name=name, education_stage=stage, grade_level="", is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("subjects", "0002_initial"),
    ]

    operations = [
        # Not reversed: learners' projects, sources and results may already
        # point at these subjects.
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
