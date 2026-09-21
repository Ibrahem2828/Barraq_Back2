# Generated manually for the pending-registration security boundary.

import django.core.validators
from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0004_user_is_verified_emailotp'),
    ]

    operations = [
        migrations.CreateModel(
            name='PendingRegistration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('normalized_email', models.EmailField(max_length=254, unique=True)),
                ('full_name', models.CharField(max_length=255)),
                (
                    'phone_number',
                    models.CharField(
                        blank=True,
                        max_length=20,
                        validators=[
                            django.core.validators.RegexValidator(
                                message='Phone number format is invalid.',
                                regex='^[0-9+\\-\\s()]{7,20}$',
                            ),
                        ],
                    ),
                ),
                ('password_hash', models.CharField(max_length=128)),
                ('otp_hash', models.CharField(max_length=128)),
                ('otp_expires_at', models.DateTimeField()),
                ('otp_attempt_count', models.PositiveSmallIntegerField(default=0)),
                ('otp_send_count', models.PositiveSmallIntegerField(default=0)),
                ('otp_send_window_started_at', models.DateTimeField()),
                ('last_otp_sent_at', models.DateTimeField()),
            ],
        ),
        migrations.AddConstraint(
            model_name='pendingregistration',
            constraint=models.UniqueConstraint(
                Lower('normalized_email'),
                name='unique_pending_registration_email_case_insensitive',
            ),
        ),
        migrations.AddIndex(
            model_name='pendingregistration',
            index=models.Index(fields=['otp_expires_at'], name='pending_reg_expiry_idx'),
        ),
        migrations.AddIndex(
            model_name='pendingregistration',
            index=models.Index(fields=['updated_at'], name='pending_reg_cleanup_idx'),
        ),
    ]
