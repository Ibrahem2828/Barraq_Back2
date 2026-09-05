from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="idempotency_key",
            field=models.CharField(blank=True, db_index=True, max_length=160),
        ),
        migrations.AddConstraint(
            model_name="notification",
            constraint=models.UniqueConstraint(fields=("user", "idempotency_key"), condition=~Q(("idempotency_key", "")), name="unique_notification_idempotency_key"),
        ),
    ]
