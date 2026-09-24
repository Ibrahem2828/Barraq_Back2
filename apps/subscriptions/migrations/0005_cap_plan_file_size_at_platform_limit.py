from django.conf import settings
from django.db import migrations

PREVIOUS_KEY = "max_file_size_mb_before_platform_cap"


def cap_file_size(apps, schema_editor):
    """No plan may promise more per file than the platform accepts.

    Production had Pro at 150MB while every upload is capped at
    STUDENT_SOURCE_MAX_UPLOAD_MB (50MB). The original figure is kept in the
    plan's limits so the change can be reversed exactly.
    """
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")
    ceiling = int(settings.STUDENT_SOURCE_MAX_UPLOAD_MB)
    for plan in SubscriptionPlan.objects.all():
        limits = dict(plan.limits or {})
        size = limits.get("max_file_size_mb")
        if isinstance(size, (int, float)) and size > ceiling:
            limits[PREVIOUS_KEY] = size
            limits["max_file_size_mb"] = ceiling
            plan.limits = limits
            plan.save(update_fields=["limits"])


def restore_file_size(apps, schema_editor):
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")
    for plan in SubscriptionPlan.objects.all():
        limits = dict(plan.limits or {})
        if PREVIOUS_KEY in limits:
            limits["max_file_size_mb"] = limits.pop(PREVIOUS_KEY)
            plan.limits = limits
            plan.save(update_fields=["limits"])


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0004_open_all_characters_on_free_plan"),
    ]

    operations = [
        migrations.RunPython(cap_file_size, restore_file_size),
    ]
