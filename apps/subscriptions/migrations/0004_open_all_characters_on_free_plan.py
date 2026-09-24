"""Open Kholasa and Sada on the free plan and raise its upload ceiling to 50MB.

`ensure_default_plans()` only runs from `bootstrap_baraq`, so the new
constants alone would not reach a database that was already seeded. Only the
keys below are merged; any other value an admin changed on the plan is kept.
"""

from django.db import migrations

FREE_PLAN_CODE = "free"

OPENED = {
    "limits": {
        "max_file_size_mb": 50,
        "max_kholasa_requests_per_month": 10,
        "max_sada_requests_per_month": 10,
    },
    "features": {"can_use_kholasa": True, "can_use_sada": True},
}

PREVIOUS = {
    "limits": {
        "max_file_size_mb": 10,
        "max_kholasa_requests_per_month": 0,
        "max_sada_requests_per_month": 0,
    },
    "features": {"can_use_kholasa": False, "can_use_sada": False},
}


def _apply(apps, values):
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")
    for plan in SubscriptionPlan.objects.filter(code=FREE_PLAN_CODE):
        plan.limits = {**(plan.limits or {}), **values["limits"]}
        plan.features = {**(plan.features or {}), **values["features"]}
        plan.save(update_fields=["limits", "features"])


def open_characters(apps, schema_editor):
    _apply(apps, OPENED)


def restore_previous(apps, schema_editor):
    _apply(apps, PREVIOUS)


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0003_usage_ledger"),
    ]

    operations = [
        migrations.RunPython(open_characters, restore_previous),
    ]
