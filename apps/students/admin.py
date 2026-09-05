from django.contrib import admin

from .models import StudentProfile


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'education_stage',
        'grade_level',
        'specialization',
        'is_setup_completed',
        'updated_at',
    )
    search_fields = ('user__email', 'user__full_name', 'specialization', 'grade_level')
    list_filter = ('is_setup_completed', 'education_stage', 'grade_level')
    readonly_fields = ('created_at', 'updated_at')
