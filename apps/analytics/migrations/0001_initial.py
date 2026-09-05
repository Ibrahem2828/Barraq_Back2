import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    initial=True
    dependencies=[migrations.swappable_dependency(settings.AUTH_USER_MODEL),('ai_integration','0001_initial'),('subjects','0002_initial')]
    operations=[
        migrations.CreateModel(
            name='StudentRecommendation',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),
                ('title',models.CharField(max_length=255)),('summary',models.TextField(blank=True)),
                ('overall_score',models.DecimalField(blank=True,decimal_places=2,max_digits=5,null=True)),
                ('strengths',models.JSONField(blank=True,default=list)),('weaknesses',models.JSONField(blank=True,default=list)),
                ('recommendations',models.JSONField(blank=True,default=list)),('next_best_action',models.JSONField(blank=True,default=dict)),
                ('source_metrics',models.JSONField(blank=True,default=dict)),('is_read',models.BooleanField(default=False)),
                ('ai_job',models.OneToOneField(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='recommendation',to='ai_integration.aijob')),
                ('subject',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='recommendations',to='subjects.subject')),
                ('user',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='recommendations',to=settings.AUTH_USER_MODEL)),
            ],options={'ordering':('-created_at',)}),
        migrations.AddIndex(model_name='studentrecommendation',index=models.Index(fields=['user','-created_at'],name='recommend_user_date_idx')),
    ]
