import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
class Migration(migrations.Migration):
    initial=True
    dependencies=[migrations.swappable_dependency(settings.AUTH_USER_MODEL),('ai_integration','0001_initial'),('sources','0002_alter_studentsourceinteraction_source_and_more')]
    operations=[migrations.CreateModel(name='Transcription',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),
        ('title',models.CharField(max_length=255)),('language',models.CharField(default='ar',max_length=20)),('full_transcript',models.TextField()),('cleaned_transcript',models.TextField(blank=True)),('segments',models.JSONField(blank=True,default=list)),('detected_topics',models.JSONField(blank=True,default=list)),('duration_seconds',models.PositiveIntegerField(default=0)),('confidence_score',models.DecimalField(blank=True,decimal_places=4,max_digits=5,null=True)),
        ('ai_job',models.OneToOneField(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='transcription',to='ai_integration.aijob')),
        ('source',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='transcriptions',to='sources.studentsource')),
        ('user',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='transcriptions',to=settings.AUTH_USER_MODEL)),
    ],options={'ordering':('-created_at',)}),migrations.AddIndex(model_name='transcription',index=models.Index(fields=['user','-created_at'],name='transcript_user_date_idx'))]
