import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('sources', '0002_alter_studentsourceinteraction_source_and_more'),
        ('subjects', '0002_initial'),
    ]
    operations = [
        migrations.CreateModel(
            name='AIJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('character', models.CharField(choices=[('fahes','فاحص'),('khota','خطى'),('rasheed','رشيد'),('kholasa','خلاصة'),('sada','صدى')], db_index=True, max_length=20)),
                ('task_type', models.CharField(choices=[('fahes_generate_quiz','Generate quiz'),('khota_generate_plan','Generate study plan'),('rasheed_recommendations','Performance recommendations'),('kholasa_summary','Summarize source'),('sada_transcription','Transcribe audio')], db_index=True, max_length=50)),
                ('status', models.CharField(choices=[('created','Created'),('queued','Queued'),('submitted','Submitted to AI service'),('processing','Processing'),('validating','Validating'),('completed','Completed'),('failed','Failed'),('canceled','Canceled')], db_index=True, default='created', max_length=20)),
                ('external_job_id', models.CharField(blank=True, db_index=True, max_length=128)),
                ('idempotency_key', models.CharField(db_index=True, max_length=128)),
                ('input_payload', models.JSONField(blank=True, default=dict)),
                ('parameters', models.JSONField(blank=True, default=dict)),
                ('result_payload', models.JSONField(blank=True, default=dict)),
                ('result_type', models.CharField(blank=True, max_length=40)),
                ('result_id', models.CharField(blank=True, max_length=64)),
                ('error_code', models.CharField(blank=True, max_length=80)),
                ('error_message', models.TextField(blank=True)),
                ('credits_reserved', models.BooleanField(default=False)),
                ('credits_committed', models.BooleanField(default=False)),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('service_metadata', models.JSONField(blank=True, default=dict)),
                ('collection', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ai_jobs', to='sources.studentsourcecollection')),
                ('source', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ai_jobs', to='sources.studentsource')),
                ('subject', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ai_jobs', to='subjects.subject')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_jobs', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ('-created_at',)},
        ),
        migrations.CreateModel(
            name='AIFeedback',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('rating', models.PositiveSmallIntegerField()),
                ('is_helpful', models.BooleanField(blank=True, null=True)),
                ('feedback_type', models.CharField(choices=[('general','General'),('incorrect','Incorrect'),('not_grounded','Not grounded'),('unclear','Unclear'),('too_easy','Too easy'),('too_hard','Too hard'),('too_long','Too long'),('too_short','Too short'),('other','Other')], default='general', max_length=30)),
                ('reason_codes', models.JSONField(blank=True, default=list)),
                ('comment', models.TextField(blank=True)),
                ('corrected_output', models.JSONField(blank=True, default=dict)),
                ('training_consent', models.BooleanField(default=False)),
                ('consent_version', models.CharField(blank=True, max_length=40)),
                ('forwarded_to_ai_service', models.BooleanField(default=False)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feedback', to='ai_integration.aijob')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_feedback', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ('-created_at',)},
        ),
        migrations.CreateModel(
            name='AIWebhookEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_id', models.CharField(max_length=128, unique=True)),
                ('event_type', models.CharField(max_length=80)),
                ('external_job_id', models.CharField(blank=True, db_index=True, max_length=128)),
                ('payload_hash', models.CharField(max_length=64)),
                ('processed', models.BooleanField(default=False)),
                ('error_message', models.TextField(blank=True)),
                ('received_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={'ordering': ('-received_at',)},
        ),
        migrations.AddConstraint(model_name='aijob', constraint=models.UniqueConstraint(fields=('user','idempotency_key'), name='unique_ai_job_idempotency_per_user')),
        migrations.AddConstraint(model_name='aijob', constraint=models.CheckConstraint(condition=models.Q(('source__isnull', True), ('collection__isnull', True), _connector='OR'), name='ai_job_single_source_target')),
        migrations.AddConstraint(model_name='aifeedback', constraint=models.UniqueConstraint(fields=('job','user'), name='unique_feedback_per_ai_job_user')),
        migrations.AddConstraint(model_name='aifeedback', constraint=models.CheckConstraint(condition=models.Q(('rating__gte', 1), ('rating__lte', 5)), name='ai_feedback_rating_1_5')),
        migrations.AddIndex(model_name='aijob', index=models.Index(fields=['user','status','-created_at'], name='ai_job_user_status_idx')),
        migrations.AddIndex(model_name='aijob', index=models.Index(fields=['task_type','status','-created_at'], name='ai_job_task_status_idx')),
    ]
