from django.core.management.base import BaseCommand

from apps.subjects.models import EducationStage, Subject

ACADEMIC_STRUCTURE = [
    {
        'name': 'الابتدائية',
        'description': 'مرحلة تأسيسية للمهارات العامة والمواد الأساسية.',
        'order': 1,
        'subjects': [
            'اللغة العربية',
            'الرياضيات',
            'العلوم',
            'التربية الإسلامية',
            'اللغة الإنجليزية',
        ],
    },
    {
        'name': 'المتوسطة',
        'description': 'مرحلة بناء المعرفة الأساسية وتوسيع المفاهيم العامة.',
        'order': 2,
        'subjects': [
            'اللغة العربية',
            'الرياضيات',
            'العلوم',
            'الدراسات الاجتماعية',
            'اللغة الإنجليزية',
        ],
    },
    {
        'name': 'الثانوية',
        'description': 'مرحلة تعميق التخصص والتحضير للاختبارات النهائية والجامعة.',
        'order': 3,
        'subjects': [
            'الرياضيات',
            'الفيزياء',
            'الكيمياء',
            'الأحياء',
            'اللغة العربية',
            'اللغة الإنجليزية',
        ],
    },
    {
        'name': 'الجامعة',
        'description': 'مرحلة المهارات الأكاديمية والتخصصات العامة التمهيدية.',
        'order': 4,
        'subjects': [
            'مهارات الدراسة',
            'اللغة الإنجليزية الأكاديمية',
            'البرمجة',
            'الإحصاء',
            'الفيزياء العامة',
            'الرياضيات العامة',
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed default education stages and subjects for local MVP environments.'

    def handle(self, *args, **options):
        created_stages = 0
        updated_stages = 0
        created_subjects = 0
        updated_subjects = 0

        for stage_data in ACADEMIC_STRUCTURE:
            stage, created = EducationStage.objects.update_or_create(
                name=stage_data['name'],
                defaults={
                    'description': stage_data['description'],
                    'order': stage_data['order'],
                    'is_active': True,
                },
            )
            if created:
                created_stages += 1
            else:
                updated_stages += 1

            for subject_name in stage_data['subjects']:
                _, subject_created = Subject.objects.update_or_create(
                    name=subject_name,
                    education_stage=stage,
                    grade_level='',
                    defaults={
                        'description': f'مادة {subject_name} ضمن مرحلة {stage.name}.',
                        'is_active': True,
                    },
                )
                if subject_created:
                    created_subjects += 1
                else:
                    updated_subjects += 1

        self.stdout.write(
            self.style.SUCCESS(
                'Academic data seeded successfully. '
                f'Stages created={created_stages}, updated={updated_stages}; '
                f'Subjects created={created_subjects}, updated={updated_subjects}.'
            )
        )
