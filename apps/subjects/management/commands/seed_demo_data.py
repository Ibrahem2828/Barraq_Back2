import os
import sys
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.admin_dashboard.services import assign_roles_to_user, seed_default_rbac
from apps.projects.models import Project
from apps.quizzes.models import (
    AttemptStatusChoices,
    Choice,
    DifficultyLevelChoices,
    GenerationTypeChoices,
    Question,
    QuestionBankItem,
    QuestionTypeChoices,
    Quiz,
    QuizAttempt,
    QuizStatusChoices,
    QuizTypeChoices,
    StudentAnswer,
)
from apps.quizzes.services import calculate_attempt_result
from apps.sources.models import (
    StudentSource,
    StudentSourceCollection,
    StudentSourceInteraction,
)
from apps.sources.services import use_collection_with_character, use_source_with_character
from apps.students.models import StudentProfile
from apps.study_plans.models import StudyPlan, StudyTask
from apps.study_plans.services import update_plan_completion
from apps.subjects.models import EducationStage, Subject, UserSubject
from apps.subscriptions.services import (
    change_user_plan,
    ensure_default_plans,
    get_or_create_user_subscription,
)

ADMIN_EMAIL = 'admin@baraq.app'
PROJECT_ADMIN_EMAIL = 'project.admin@baraq.app'
STUDENT_EMAIL = 'student@baraq.app'
GRADE_LEVEL = 'الثالث الثانوي'

DEMO_PASSWORD_ENVIRONMENT_VARIABLES = {
    'admin': 'BARAQ_DEMO_ADMIN_PASSWORD',
    'project_admin': 'BARAQ_DEMO_PROJECT_ADMIN_PASSWORD',
    'student': 'BARAQ_DEMO_STUDENT_PASSWORD',
}


STAGES = [
    {
        'name': 'المرحلة الإعدادية',
        'description': 'مرحلة تأسيسية لترسيخ المفاهيم والمهارات الدراسية الأساسية.',
        'order': 1,
    },
    {
        'name': 'المرحلة الثانوية',
        'description': 'مرحلة التحضير للامتحانات النهائية وبناء الجاهزية الجامعية.',
        'order': 2,
    },
    {
        'name': 'المرحلة الجامعية',
        'description': 'مرحلة تطوير المهارات الأكاديمية والتخصصية للطلاب الجامعيين.',
        'order': 3,
    },
]


SUBJECTS = [
    {
        'name': 'الرياضيات',
        'stage': 'المرحلة الثانوية',
        'grade_level': GRADE_LEVEL,
        'description': 'جبر وتفاضل ونهايات وتدريب على حل المسائل خطوة بخطوة.',
    },
    {
        'name': 'الفيزياء',
        'stage': 'المرحلة الثانوية',
        'grade_level': GRADE_LEVEL,
        'description': 'قوانين الحركة والطاقة والكهرباء مع تطبيقات امتحانية.',
    },
    {
        'name': 'الكيمياء',
        'stage': 'المرحلة الثانوية',
        'grade_level': GRADE_LEVEL,
        'description': 'الروابط الكيميائية والجدول الدوري والأحماض والقواعد.',
    },
    {
        'name': 'علم الأحياء',
        'stage': 'المرحلة الثانوية',
        'grade_level': GRADE_LEVEL,
        'description': 'مراجعة الأنظمة الحيوية والمفاهيم الأساسية في الأحياء.',
    },
    {
        'name': 'اللغة العربية',
        'stage': 'المرحلة الثانوية',
        'grade_level': GRADE_LEVEL,
        'description': 'النصوص والقواعد والإعراب والتعبير الكتابي.',
    },
    {
        'name': 'اللغة الإنجليزية',
        'stage': 'المرحلة الثانوية',
        'grade_level': GRADE_LEVEL,
        'description': 'قراءة وقواعد ومفردات وتدريب على الأسئلة القصيرة.',
    },
    {
        'name': 'التاريخ',
        'stage': 'المرحلة الثانوية',
        'grade_level': GRADE_LEVEL,
        'description': 'مراجعة الأحداث التاريخية وربطها بالسياق والزمن.',
    },
    {
        'name': 'الجغرافيا',
        'stage': 'المرحلة الثانوية',
        'grade_level': GRADE_LEVEL,
        'description': 'خرائط ومناخ وسكان وموارد بأسلوب مراجعة منظم.',
    },
    {
        'name': 'الرياضيات',
        'stage': 'المرحلة الإعدادية',
        'grade_level': 'الصف التاسع',
        'description': 'مفاهيم رياضية تأسيسية للمرحلة الإعدادية.',
    },
    {
        'name': 'العلوم',
        'stage': 'المرحلة الإعدادية',
        'grade_level': 'الصف التاسع',
        'description': 'علوم عامة وتمارين مبسطة لطلاب الصف التاسع.',
    },
    {
        'name': 'مهارات الدراسة',
        'stage': 'المرحلة الجامعية',
        'grade_level': 'السنة الأولى',
        'description': 'تنظيم الوقت وتدوين الملاحظات والاستعداد للاختبارات.',
    },
]


SELECTED_SUBJECTS = ['الرياضيات', 'الفيزياء', 'الكيمياء', 'اللغة العربية']


QUIZZES = [
    {
        'title': 'اختبار سريع في التفاضل',
        'subject': 'الرياضيات',
        'description': 'اختبار قصير لقياس فهم أساسيات التفاضل والنهايات.',
        'topic': 'التفاضل',
        'difficulty_level': DifficultyLevelChoices.MEDIUM,
        'quiz_type': QuizTypeChoices.QUICK,
        'time_limit_minutes': 10,
        'questions': [
            {
                'text': 'ما مشتقة x^2؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'قاعدة القوة تقول إن مشتقة x^n هي n*x^(n-1).',
                'choices': [('2x', True), ('x', False), ('x^2', False), ('1', False)],
            },
            {
                'text': 'قيمة نهاية ثابتة c عندما x يقترب من 0 هي c.',
                'question_type': QuestionTypeChoices.TRUE_FALSE,
                'explanation': 'نهاية الدالة الثابتة تساوي نفس الثابت.',
                'choices': [('صحيح', True), ('خطأ', False)],
            },
            {
                'text': 'إذا كانت f(x)=3x، فما قيمة f\'(x)؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'مشتقة الدالة الخطية ax تساوي a.',
                'choices': [('3', True), ('x', False), ('3x', False), ('0', False)],
            },
            {
                'text': 'ما مشتقة الدالة الثابتة؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'معدل تغير الدالة الثابتة يساوي صفرًا.',
                'choices': [('0', True), ('1', False), ('x', False), ('غير موجودة', False)],
            },
            {
                'text': 'تستخدم النهايات لفهم سلوك الدالة قرب نقطة معينة.',
                'question_type': QuestionTypeChoices.TRUE_FALSE,
                'explanation': 'النهاية تصف سلوك الدالة عندما يقترب المتغير من قيمة محددة.',
                'choices': [('صحيح', True), ('خطأ', False)],
            },
        ],
    },
    {
        'title': 'اختبار قوانين الحركة',
        'subject': 'الفيزياء',
        'description': 'تدريب على قوانين نيوتن والطاقة ووحدات القياس.',
        'topic': 'الحركة والطاقة',
        'difficulty_level': DifficultyLevelChoices.HARD,
        'quiz_type': QuizTypeChoices.PRACTICE,
        'time_limit_minutes': 15,
        'questions': [
            {
                'text': 'وحدة القوة في النظام الدولي هي؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'تقاس القوة بوحدة نيوتن في النظام الدولي.',
                'choices': [('نيوتن', True), ('جول', False), ('واط', False), ('باسكال', False)],
            },
            {
                'text': 'كلما زادت الكتلة مع ثبات القوة قل التسارع.',
                'question_type': QuestionTypeChoices.TRUE_FALSE,
                'explanation': 'حسب القانون الثاني لنيوتن: التسارع يساوي القوة مقسومة على الكتلة.',
                'choices': [('صحيح', True), ('خطأ', False)],
            },
            {
                'text': 'الطاقة الحركية تعتمد على؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'الطاقة الحركية تعتمد على الكتلة ومربع السرعة.',
                'choices': [('الكتلة والسرعة', True), ('اللون', False), ('درجة الحرارة فقط', False), ('الحجم فقط', False)],
            },
            {
                'text': 'ما القانون الذي يربط القوة بالكتلة والتسارع؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'القانون الثاني لنيوتن هو F = m a.',
                'choices': [('F = m a', True), ('P = V I', False), ('E = h f', False), ('V = I R', False)],
            },
            {
                'text': 'السرعة المتوسطة تساوي المسافة مقسومة على الزمن.',
                'question_type': QuestionTypeChoices.TRUE_FALSE,
                'explanation': 'السرعة المتوسطة تحسب من العلاقة v = d / t.',
                'choices': [('صحيح', True), ('خطأ', False)],
            },
            {
                'text': 'الجول وحدة قياس ماذا؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'الجول وحدة قياس الطاقة والشغل.',
                'choices': [('الطاقة', True), ('القوة', False), ('الضغط', False), ('الشحنة', False)],
            },
        ],
    },
    {
        'title': 'اختبار الروابط الكيميائية',
        'subject': 'الكيمياء',
        'description': 'مراجعة سريعة للروابط الأيونية والتساهمية.',
        'topic': 'الروابط',
        'difficulty_level': DifficultyLevelChoices.MEDIUM,
        'quiz_type': QuizTypeChoices.PRACTICE,
        'time_limit_minutes': 12,
        'questions': [
            {
                'text': 'الرابطة الأيونية تنتج غالبًا بين؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'تتكون الرابطة الأيونية غالبًا بين فلز ولافلز بسبب انتقال الإلكترونات.',
                'choices': [('فلز ولافلز', True), ('فلزين', False), ('لافلزين', False), ('غازين خاملين', False)],
            },
            {
                'text': 'الرابطة التساهمية تعتمد على مشاركة الإلكترونات.',
                'question_type': QuestionTypeChoices.TRUE_FALSE,
                'explanation': 'في الرابطة التساهمية تشترك الذرات بزوج أو أكثر من الإلكترونات.',
                'choices': [('صحيح', True), ('خطأ', False)],
            },
            {
                'text': 'أي الجسيمات تحمل شحنة سالبة؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'الإلكترون يحمل شحنة سالبة.',
                'choices': [('الإلكترون', True), ('البروتون', False), ('النيوترون', False), ('النواة', False)],
            },
            {
                'text': 'الأحماض غالبًا تعطي أيون الهيدروجين في المحلول.',
                'question_type': QuestionTypeChoices.TRUE_FALSE,
                'explanation': 'تتميز الأحماض بزيادة تركيز أيونات الهيدروجين في المحلول.',
                'choices': [('صحيح', True), ('خطأ', False)],
            },
            {
                'text': 'يرتب الجدول الدوري العناصر حسب؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'يرتب الجدول الدوري العناصر حسب العدد الذري.',
                'choices': [('العدد الذري', True), ('الحجم فقط', False), ('اللون', False), ('درجة الغليان فقط', False)],
            },
        ],
    },
    {
        'title': 'اختبار الإعراب والنصوص',
        'subject': 'اللغة العربية',
        'description': 'اختبار مبسط في الإعراب وفهم النصوص.',
        'topic': 'القواعد',
        'difficulty_level': DifficultyLevelChoices.EASY,
        'quiz_type': QuizTypeChoices.PRACTICE,
        'time_limit_minutes': 10,
        'questions': [
            {
                'text': 'الفاعل مرفوع دائمًا.',
                'question_type': QuestionTypeChoices.TRUE_FALSE,
                'explanation': 'الأصل في الفاعل أن يكون مرفوعًا.',
                'choices': [('صحيح', True), ('خطأ', False)],
            },
            {
                'text': 'ما علامة رفع جمع المذكر السالم؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'يرفع جمع المذكر السالم بالواو.',
                'choices': [('الواو', True), ('الألف', False), ('الياء', False), ('الفتحة', False)],
            },
            {
                'text': 'المفعول به منصوب.',
                'question_type': QuestionTypeChoices.TRUE_FALSE,
                'explanation': 'المفعول به من المنصوبات.',
                'choices': [('صحيح', True), ('خطأ', False)],
            },
            {
                'text': 'أي مما يلي حرف جر؟',
                'question_type': QuestionTypeChoices.MCQ,
                'explanation': 'من حروف الجر: من، إلى، عن، على.',
                'choices': [('من', True), ('كتب', False), ('طالب', False), ('جميل', False)],
            },
            {
                'text': 'الفكرة العامة للنص تساعد على فهم التفاصيل.',
                'question_type': QuestionTypeChoices.TRUE_FALSE,
                'explanation': 'تحديد الفكرة العامة يسهل ترتيب المعاني والتفاصيل.',
                'choices': [('صحيح', True), ('خطأ', False)],
            },
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed complete Arabic demo data for the Baraq MVP backend.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--allow-demo-data',
            action='store_true',
            help='Acknowledge that this destructive-to-demo-only command is for a non-production environment.',
        )
        parser.add_argument(
            '--reset-demo',
            action='store_true',
            help='Delete only demo users and their owned demo data before reseeding.',
        )
        parser.add_argument(
            '--reset-demo-passwords',
            action='store_true',
            help='Reset passwords for existing demo users.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not options['allow_demo_data']:
            raise CommandError(
                'Refusing to seed demo data without --allow-demo-data. '
                'This command must never be used for a production deployment.'
            )

        self._demo_passwords = self._read_demo_passwords()

        if options['reset_demo']:
            self._reset_demo_data()

        User = get_user_model()
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())

        stages = self._seed_stages()
        subjects = self._seed_subjects(stages)
        arabic_subjects = self._ensure_required_arabic_demo_catalog()
        permissions, roles = seed_default_rbac()
        subscription_plans = ensure_default_plans()
        admin = self._seed_admin(User, roles, options['reset_demo_passwords'])
        project_admin = self._seed_project_admin(
            User,
            roles,
            admin,
            options['reset_demo_passwords'],
        )
        student = self._seed_student(User, options['reset_demo_passwords'])
        student_subscription = get_or_create_user_subscription(student)
        if student_subscription.plan_id != subscription_plans['free'].id:
            student_subscription = change_user_plan(
                student,
                subscription_plans['free'],
                actor=admin,
                metadata={'demo_seed': True},
            )
        get_or_create_user_subscription(project_admin)
        self._seed_student_profile(student, stages['المرحلة الثانوية'])
        selected_subjects_count = self._seed_user_subjects(student, subjects)
        plans = self._seed_study_plans(student, subjects, today, week_start)
        tasks_count = self._seed_study_tasks(plans, today, week_start)
        quizzes = self._seed_quizzes(student, subjects)
        questions_count, choices_count = self._seed_questions(quizzes)
        attempt_created = self._seed_demo_attempt(student, quizzes['اختبار سريع في التفاضل'])
        demo_project = self._seed_demo_project(student, arabic_subjects['الرياضيات'])
        demo_sources_count, demo_interactions_count = self._seed_demo_sources_and_interactions(
            student,
            arabic_subjects,
            demo_project,
        )

        self.stdout.write(self.style.SUCCESS('Demo data seeded successfully.'))
        self.stdout.write(f'Education stages: {len(stages)}')
        self.stdout.write(f'Subjects: {len(subjects)}')
        self.stdout.write(f'Selected student subjects: {selected_subjects_count}')
        self.stdout.write(f'Study plans: {len(plans)}')
        self.stdout.write(f'Study tasks: {tasks_count}')
        self.stdout.write(f'Quizzes: {len(quizzes)}')
        self.stdout.write(f'Questions: {questions_count}')
        self.stdout.write(f'Choices: {choices_count}')
        self.stdout.write(f'Demo submitted attempt: {"yes" if attempt_created else "no"}')
        self.stdout.write(f'Demo student sources: {demo_sources_count}')
        self.stdout.write(f'Demo source interactions: {demo_interactions_count}')
        self.stdout.write(f'Admin permissions: {len(permissions)}')
        self.stdout.write(f'Admin roles: {len(roles)}')
        self.stdout.write(
            'Subscription plans: '
            + ', '.join(plan.code for plan in subscription_plans.values())
        )
        self.stdout.write(
            f'Demo student subscription: {student.email} -> {student_subscription.plan.code}'
        )
        self.stdout.write('')
        self.stdout.write(
            self.style.WARNING(
                'Demo identities were created with passwords supplied by environment variables; passwords are never logged.'
            )
        )

    def _read_demo_passwords(self):
        passwords = {
            identity: os.environ.get(environment_variable, '')
            for identity, environment_variable in DEMO_PASSWORD_ENVIRONMENT_VARIABLES.items()
        }
        missing = [
            environment_variable
            for identity, environment_variable in DEMO_PASSWORD_ENVIRONMENT_VARIABLES.items()
            if not passwords[identity]
        ]
        if missing:
            raise CommandError(
                'Demo credentials were not supplied. Set the following non-empty environment variable(s): '
                + ', '.join(missing)
            )
        return passwords

    def _reset_demo_data(self):
        demo_emails = [ADMIN_EMAIL, PROJECT_ADMIN_EMAIL, STUDENT_EMAIL]
        QuestionBankItem.objects.filter(created_by__email__in=demo_emails).delete()
        get_user_model().objects.filter(email__in=demo_emails).delete()
        self.stdout.write(self.style.WARNING('Deleted demo users and their owned demo data.'))

    def _seed_stages(self):
        stages = {}
        for payload in STAGES:
            stage, created = EducationStage.objects.update_or_create(
                name=payload['name'],
                defaults={
                    'description': payload['description'],
                    'order': payload['order'],
                    'is_active': True,
                },
            )
            stages[stage.name] = stage
            self._write_upsert('stage', stage.name, created)
        return stages

    def _seed_subjects(self, stages):
        subjects = {}
        for payload in SUBJECTS:
            stage = stages[payload['stage']]
            subject, created = Subject.objects.update_or_create(
                name=payload['name'],
                education_stage=stage,
                grade_level=payload['grade_level'],
                defaults={
                    'description': payload['description'],
                    'is_active': True,
                },
            )
            subjects[(subject.name, subject.grade_level)] = subject
            self._write_upsert('subject', f'{subject.name} ({subject.grade_level})', created)
        return subjects

    def _ensure_required_arabic_demo_catalog(self):
        stage_payloads = [
            ('المرحلة الإعدادية', 1),
            ('المرحلة الثانوية', 2),
            ('المرحلة الجامعية', 3),
        ]
        stages = {}
        for name, order in stage_payloads:
            stage, created = EducationStage.objects.update_or_create(
                name=name,
                defaults={
                    'description': f'{name} ضمن بيانات برّاق التجريبية.',
                    'order': order,
                    'is_active': True,
                },
            )
            stages[name] = stage
            self._write_upsert('stage', stage.name, created)

        subject_names = [
            'الرياضيات',
            'الفيزياء',
            'الكيمياء',
            'اللغة العربية',
            'اللغة الإنجليزية',
            'الأحياء',
        ]
        subjects = {}
        for name in subject_names:
            subject, created = Subject.objects.update_or_create(
                name=name,
                education_stage=stages['المرحلة الثانوية'],
                grade_level='الثالث الثانوي',
                defaults={
                    'description': f'مادة {name} ضمن بيانات برّاق التجريبية.',
                    'is_active': True,
                },
            )
            subjects[name] = subject
            self._write_upsert('subject', f'{subject.name} ({subject.grade_level})', created)
        return subjects

    def _seed_admin(self, User, roles, reset_password):
        admin, created = User.objects.update_or_create(
            email=ADMIN_EMAIL,
            defaults={
                'full_name': 'مدير برّاق',
                'phone_number': '+963900000001',
                'role': User.Roles.SUPER_ADMIN,
                'is_active': True,
                'is_staff': True,
                'is_superuser': True,
            },
        )
        if created or reset_password:
            admin.set_password(self._demo_passwords['admin'])
            admin.save()
        assign_roles_to_user(admin, [roles['super_admin']], assigned_by=admin)
        self._write_upsert('admin user', admin.email, created)
        return admin

    def _seed_project_admin(self, User, roles, assigned_by, reset_password):
        project_admin, created = User.objects.update_or_create(
            email=PROJECT_ADMIN_EMAIL,
            defaults={
                'full_name': 'مدير مشروع برّاق',
                'phone_number': '+963900000002',
                'role': User.Roles.ADMIN,
                'is_active': True,
                'is_staff': True,
                'is_superuser': False,
            },
        )
        if created or reset_password:
            project_admin.set_password(self._demo_passwords['project_admin'])
            project_admin.save()
        assign_roles_to_user(project_admin, [roles['admin']], assigned_by=assigned_by)
        self._write_upsert('project admin user', project_admin.email, created)
        return project_admin

    def _seed_student(self, User, reset_password):
        student, created = User.objects.update_or_create(
            email=STUDENT_EMAIL,
            defaults={
                'full_name': 'طالب برّاق',
                'phone_number': '+963900000000',
                'role': User.Roles.STUDENT,
                'is_active': True,
                'is_staff': False,
                'is_superuser': False,
            },
        )
        if created or reset_password:
            student.set_password(self._demo_passwords['student'])
            student.save()
        self._write_upsert('student user', student.email, created)
        return student

    def _seed_student_profile(self, student, secondary_stage):
        profile, created = StudentProfile.objects.update_or_create(
            user=student,
            defaults={
                'education_stage': secondary_stage,
                'grade_level': GRADE_LEVEL,
                'specialization': 'علمي',
                'study_goal': 'التحضير للامتحان النهائي ورفع مستوى التحصيل',
                'daily_study_hours': 3,
                'is_setup_completed': True,
            },
        )
        self._write_upsert('student profile', profile.user.email, created)
        return profile

    def _seed_demo_project(self, student, math_subject):
        """Create the workspace required by the current source/AI contract."""

        project, created = Project.objects.update_or_create(
            owner=student,
            title='مشروع مراجعة الرياضيات',
            defaults={
                'subject': math_subject,
                'goal': 'مراجعة التفاضل والنهايات استعدادًا للاختبار.',
                'education_context': {'demo_seed': True},
                'status': Project.Status.ACTIVE,
                'color': '#2563eb',
                'icon': 'calculator',
            },
        )
        self._write_upsert('student project', project.title, created)
        return project

    def _seed_user_subjects(self, student, subjects):
        count = 0
        for subject_name in SELECTED_SUBJECTS:
            subject = subjects[(subject_name, GRADE_LEVEL)]
            _, created = UserSubject.objects.get_or_create(user=student, subject=subject)
            count += 1
            self._write_upsert('user subject', subject.name, created)
        return count

    def _seed_study_plans(self, student, subjects, today, week_start):
        plan_payloads = [
            {
                'title': 'خطة مراجعة الرياضيات الأسبوعية',
                'description': 'مراجعة مركزة لهذا الأسبوع مع تمارين قصيرة يومية.',
                'subject': subjects[('الرياضيات', GRADE_LEVEL)],
                'status': StudyPlan.Status.ACTIVE,
                'difficulty_level': StudyPlan.DifficultyLevel.MEDIUM,
                'start_date': week_start,
                'end_date': week_start + timedelta(days=7),
                'daily_study_minutes': 90,
                'goal': 'حل مسائل الجبر والتفاضل وتحسين السرعة',
            },
            {
                'title': 'خطة الفيزياء للامتحان',
                'description': 'مراجعة قوانين الحركة والطاقة والكهرباء قبل الاختبار.',
                'subject': subjects[('الفيزياء', GRADE_LEVEL)],
                'status': StudyPlan.Status.ACTIVE,
                'difficulty_level': StudyPlan.DifficultyLevel.HARD,
                'start_date': today,
                'end_date': today + timedelta(days=10),
                'daily_study_minutes': 75,
                'goal': 'مراجعة قوانين الحركة والطاقة والكهرباء',
            },
            {
                'title': 'خطة الكيمياء القصيرة',
                'description': 'خطة سريعة لتثبيت المفاهيم الأساسية في الكيمياء.',
                'subject': subjects[('الكيمياء', GRADE_LEVEL)],
                'status': StudyPlan.Status.ACTIVE,
                'difficulty_level': StudyPlan.DifficultyLevel.MEDIUM,
                'start_date': today,
                'end_date': today + timedelta(days=5),
                'daily_study_minutes': 60,
                'goal': 'تثبيت مفاهيم الروابط الكيميائية والأحماض والقواعد',
            },
            {
                'title': 'خطة اللغة العربية',
                'description': 'مراجعة النصوص والإعراب والتعبير الكتابي.',
                'subject': subjects[('اللغة العربية', GRADE_LEVEL)],
                'status': StudyPlan.Status.ACTIVE,
                'difficulty_level': StudyPlan.DifficultyLevel.EASY,
                'start_date': today - timedelta(days=7),
                'end_date': today,
                'daily_study_minutes': 45,
                'goal': 'تحسين الإعراب وفهم النصوص وكتابة موضوع قصير',
            },
        ]
        plans = {}
        for payload in plan_payloads:
            title = payload.pop('title')
            plan, created = StudyPlan.objects.update_or_create(
                user=student,
                title=title,
                defaults={**payload, 'generation_type': StudyPlan.GenerationType.MANUAL},
            )
            plans[title] = plan
            self._write_upsert('study plan', plan.title, created)
        return plans

    def _seed_study_tasks(self, plans, today, week_start):
        task_payloads = {
            'خطة مراجعة الرياضيات الأسبوعية': [
                (week_start, 2, 'مراجعة قوانين الاشتقاق', 'اقرأ القوانين الأساسية واكتب مثالًا لكل قانون.', 45, StudyTask.Priority.MEDIUM, StudyTask.Status.COMPLETED),
                (today, 1, 'حل 20 مسألة تفاضل', 'حل مسائل متنوعة مع تسجيل الأخطاء المتكررة.', 60, StudyTask.Priority.HIGH, StudyTask.Status.PENDING),
                (today + timedelta(days=1), 1, 'تلخيص أخطاء التمارين السابقة', 'راجع المسائل التي أخطأت بها واكتب سبب الخطأ.', 30, StudyTask.Priority.MEDIUM, StudyTask.Status.IN_PROGRESS),
                (today + timedelta(days=2), 1, 'اختبار قصير على النهايات', 'اختبر نفسك في خمس مسائل نهايات دون الرجوع للملخص.', 30, StudyTask.Priority.MEDIUM, StudyTask.Status.PENDING),
                (week_start + timedelta(days=4), 2, 'مراجعة الجبر الأساسي', 'راجع التحليل والمعادلات السريعة.', 45, StudyTask.Priority.LOW, StudyTask.Status.COMPLETED),
            ],
            'خطة الفيزياء للامتحان': [
                (today, 1, 'مراجعة قوانين نيوتن', 'اكتب القوانين الثلاثة مع مثال تطبيقي لكل قانون.', 45, StudyTask.Priority.HIGH, StudyTask.Status.PENDING),
                (today + timedelta(days=1), 1, 'حل مسائل الطاقة', 'حل مسائل الطاقة الحركية والوضعية من دفتر التمارين.', 50, StudyTask.Priority.HIGH, StudyTask.Status.PENDING),
                (today + timedelta(days=2), 1, 'مراجعة الكهرباء الساكنة', 'لخص الشحنة والقوة الكهربائية والمجال الكهربائي.', 40, StudyTask.Priority.MEDIUM, StudyTask.Status.PENDING),
                (today + timedelta(days=3), 1, 'تدريب على مسائل الزمن والسرعة', 'حل مجموعة قصيرة بزمن محدد لتحسين السرعة.', 35, StudyTask.Priority.MEDIUM, StudyTask.Status.PENDING),
            ],
            'خطة الكيمياء القصيرة': [
                (today, 1, 'مراجعة الجدول الدوري', 'حدد المجموعات المهمة واتجاهات الخواص الدورية.', 35, StudyTask.Priority.MEDIUM, StudyTask.Status.PENDING),
                (today + timedelta(days=1), 1, 'حل أسئلة الروابط الكيميائية', 'حل أسئلة اختيار من متعدد حول الأيونية والتساهمية.', 40, StudyTask.Priority.HIGH, StudyTask.Status.PENDING),
                (today + timedelta(days=2), 1, 'تلخيص الأحماض والقواعد', 'اكتب فروقات مختصرة وأمثلة شائعة.', 30, StudyTask.Priority.MEDIUM, StudyTask.Status.SKIPPED),
            ],
            'خطة اللغة العربية': [
                (today - timedelta(days=6), 1, 'مراجعة النصوص', 'اقرأ نصًا وحدد الفكرة العامة والأفكار الجزئية.', 35, StudyTask.Priority.LOW, StudyTask.Status.COMPLETED),
                (today - timedelta(days=3), 1, 'تدريب على الإعراب', 'أعرب خمس جمل وحدد الأخطاء المتكررة.', 45, StudyTask.Priority.MEDIUM, StudyTask.Status.COMPLETED),
                (today, 1, 'كتابة موضوع قصير', 'اكتب موضوعًا من 150 كلمة مع مقدمة وخاتمة.', 45, StudyTask.Priority.MEDIUM, StudyTask.Status.PENDING),
            ],
        }
        count = 0
        for plan_title, tasks in task_payloads.items():
            plan = plans[plan_title]
            task_ids = []
            for task_date, order, title, description, minutes, priority, task_status in tasks:
                completed_at = timezone.now() - timedelta(days=1) if task_status == StudyTask.Status.COMPLETED else None
                task, created = StudyTask.objects.update_or_create(
                    plan=plan,
                    task_date=task_date,
                    order=order,
                    defaults={
                        'title': title,
                        'description': description,
                        'estimated_minutes': minutes,
                        'priority': priority,
                        'status': task_status,
                        'completed_at': completed_at,
                    },
                )
                task_ids.append(task.id)
                count += 1
                self._write_upsert('study task', f'{plan.title}: {task.title}', created)

            plan.tasks.exclude(id__in=task_ids).filter(title__in=[task[2] for task in tasks]).delete()
            update_plan_completion(plan)
        return count

    def _seed_quizzes(self, student, subjects):
        quizzes = {}
        for payload in QUIZZES:
            subject = subjects[(payload['subject'], GRADE_LEVEL)]
            quiz, created = Quiz.objects.update_or_create(
                user=student,
                title=payload['title'],
                defaults={
                    'subject': subject,
                    'description': payload['description'],
                    'topic': payload['topic'],
                    'difficulty_level': payload['difficulty_level'],
                    'quiz_type': payload['quiz_type'],
                    'generation_type': GenerationTypeChoices.MANUAL,
                    'status': QuizStatusChoices.PUBLISHED,
                    'questions_count': len(payload['questions']),
                    'time_limit_minutes': payload['time_limit_minutes'],
                },
            )
            quizzes[quiz.title] = quiz
            self._write_upsert('quiz', quiz.title, created)
        return quizzes

    def _seed_questions(self, quizzes):
        questions_count = 0
        choices_count = 0
        for quiz_payload in QUIZZES:
            quiz = quizzes[quiz_payload['title']]
            question_ids = []
            for question_order, question_payload in enumerate(quiz_payload['questions'], start=1):
                question, created = Question.objects.update_or_create(
                    quiz=quiz,
                    order=question_order,
                    defaults={
                        'text': question_payload['text'],
                        'question_type': question_payload['question_type'],
                        'difficulty_level': quiz.difficulty_level,
                        'explanation': question_payload['explanation'],
                        'points': 1,
                    },
                )
                question_ids.append(question.id)
                questions_count += 1
                self._write_upsert('question', f'{quiz.title} #{question.order}', created)

                choice_ids = []
                for choice_order, (choice_text, is_correct) in enumerate(question_payload['choices'], start=1):
                    choice, choice_created = Choice.objects.update_or_create(
                        question=question,
                        order=choice_order,
                        defaults={
                            'text': choice_text,
                            'is_correct': is_correct,
                        },
                    )
                    choice_ids.append(choice.id)
                    choices_count += 1
                    self._write_upsert('choice', f'{quiz.title} #{question.order}.{choice.order}', choice_created)

                question.choices.exclude(id__in=choice_ids).delete()
                self._seed_question_bank_item(quiz, question)

            quiz.questions.exclude(id__in=question_ids).delete()
            quiz.questions_count = quiz.questions.count()
            quiz.save(update_fields=['questions_count', 'updated_at'])
        return questions_count, choices_count

    def _seed_question_bank_item(self, quiz, question):
        QuestionBankItem.objects.update_or_create(
            subject=quiz.subject,
            created_by=quiz.user,
            text=question.text,
            defaults={
                'question_type': question.question_type,
                'difficulty_level': question.difficulty_level,
                'explanation': question.explanation,
                'metadata': {
                    'demo_seed': True,
                    'quiz_title': quiz.title,
                    'question_order': question.order,
                    'choices': [
                        {
                            'text': choice.text,
                            'order': choice.order,
                            'is_correct': choice.is_correct,
                        }
                        for choice in question.choices.order_by('order', 'id')
                    ],
                },
                'is_public': False,
                'is_active': True,
            },
        )

    def _seed_demo_attempt(self, student, quiz):
        attempt = (
            QuizAttempt.objects.filter(user=student, quiz=quiz, status=AttemptStatusChoices.SUBMITTED)
            .order_by('id')
            .first()
        )
        if attempt is None:
            attempt = QuizAttempt.objects.create(
                user=student,
                quiz=quiz,
                status=AttemptStatusChoices.SUBMITTED,
                started_at=timezone.now() - timedelta(minutes=12),
            )

        for index, question in enumerate(quiz.questions.prefetch_related('choices').order_by('order', 'id')):
            correct_choice = question.choices.filter(is_correct=True).order_by('order', 'id').first()
            wrong_choice = question.choices.filter(is_correct=False).order_by('order', 'id').first()
            selected_choice = correct_choice if index in {0, 1, 3} else wrong_choice or correct_choice
            is_correct = selected_choice == correct_choice
            StudentAnswer.objects.update_or_create(
                attempt=attempt,
                question=question,
                defaults={
                    'selected_choice': selected_choice,
                    'text_answer': '',
                    'is_correct': is_correct,
                    'points_awarded': Decimal(str(question.points if is_correct else 0)),
                },
            )

        result = calculate_attempt_result(attempt)
        submitted_at = timezone.now() - timedelta(minutes=2)
        attempt.status = AttemptStatusChoices.SUBMITTED
        attempt.submitted_at = submitted_at
        attempt.score = result['score']
        attempt.max_score = result['max_score']
        attempt.percentage = result['percentage']
        attempt.correct_answers_count = result['correct_answers_count']
        attempt.wrong_answers_count = result['wrong_answers_count']
        attempt.unanswered_count = result['unanswered_count']
        attempt.duration_seconds = max(int((submitted_at - attempt.started_at).total_seconds()), 0)
        attempt.save()
        return True

    def _seed_demo_sources_and_interactions(self, student, subjects, project):
        math_subject = subjects['الرياضيات']

        math_collection, math_collection_created = StudentSourceCollection.objects.update_or_create(
            user=student,
            name='الرياضيات',
            defaults={
                'project': project,
                'subject': math_subject,
                'description': 'مصادر وملاحظات الرياضيات',
                'color': '#2563eb',
                'icon': 'calculator',
                'status': StudentSourceCollection.Status.ACTIVE,
            },
        )
        self._write_upsert('student source collection', math_collection.name, math_collection_created)

        ai_collection, ai_collection_created = StudentSourceCollection.objects.update_or_create(
            user=student,
            name='محاضرات الذكاء الاصطناعي',
            defaults={
                'project': project,
                'subject': None,
                'description': 'محاضرات وملخصات الذكاء الاصطناعي',
                'color': '#7c3aed',
                'icon': 'brain',
                'status': StudentSourceCollection.Status.ACTIVE,
            },
        )
        self._write_upsert('student source collection', ai_collection.name, ai_collection_created)

        source_payloads = [
            {
                'collection': math_collection,
                'subject': math_subject,
                'title': 'ملخص النهايات والمشتقات',
                'description': 'ملخص نصي جاهز للتجربة داخل مجلد الرياضيات.',
                'filename': 'demo_math_limits_derivatives.txt',
                'text': (
                    'ملخص النهايات والمشتقات\n'
                    'النهايات تساعد على فهم سلوك الدالة قرب نقطة محددة.\n'
                    'مشتقة x^2 هي 2x، وتستخدم المشتقات لقياس معدل التغير.\n'
                    'عند حل مسائل التفاضل يجب تحديد القاعدة المناسبة ثم التحقق من الناتج.\n'
                ),
            },
            {
                'collection': ai_collection,
                'subject': None,
                'title': 'محاور ورشة الذكاء الاصطناعي للطلاب',
                'description': 'مصدر نصي جاهز عن محاور ورشة الذكاء الاصطناعي.',
                'filename': 'demo_ai_workshop_topics.txt',
                'text': (
                    'محاور ورشة الذكاء الاصطناعي للطلاب\n'
                    'مقدمة في النماذج اللغوية وكيف تساعد الطلاب على تنظيم التعلم.\n'
                    'أمثلة على صياغة الأسئلة، تلخيص الملاحظات، وتحويل الأفكار إلى خطة عمل.\n'
                    'تنبيه مهم: يجب مراجعة المخرجات وعدم مشاركة البيانات الحساسة.\n'
                ),
            },
        ]

        created_sources = 0
        created_interactions = 0
        seeded_sources = []
        for payload in source_payloads:
            source, created = StudentSource.objects.update_or_create(
                user=student,
                title=payload['title'],
                defaults={
                    'project': project,
                    'collection': payload['collection'],
                    'subject': payload['subject'],
                    'description': payload['description'],
                    'source_type': StudentSource.SourceType.TEXT,
                    'original_filename': payload['filename'],
                    'mime_type': 'text/plain',
                    'extension': 'txt',
                    'status': StudentSource.Status.READY,
                    'extracted_text': payload['text'],
                    'metadata': {'demo_seed': True},
                },
            )
            if created or not source.file:
                content = ContentFile(payload['text'].encode('utf-8'), name=payload['filename'])
                source.file.save(payload['filename'], content, save=False)
            source.file_size = source.file.size if source.file else len(payload['text'].encode('utf-8'))
            source.status = StudentSource.Status.READY
            source.save()
            seeded_sources.append(source)
            created_sources += 1
            self._write_upsert('student source', source.title, created)

        if not StudentSourceInteraction.objects.filter(
            user=student,
            collection=math_collection,
            character=StudentSourceInteraction.Character.RASHEED,
        ).exists():
            use_collection_with_character(
                student,
                math_collection,
                StudentSourceInteraction.Character.RASHEED,
            )
            created_interactions += 1

        if not StudentSourceInteraction.objects.filter(
            user=student,
            collection=math_collection,
            character=StudentSourceInteraction.Character.KHOTA,
        ).exists():
            use_collection_with_character(
                student,
                math_collection,
                StudentSourceInteraction.Character.KHOTA,
            )
            created_interactions += 1

        if not StudentSourceInteraction.objects.filter(
            user=student,
            source=seeded_sources[0],
            character=StudentSourceInteraction.Character.FAHES,
        ).exists():
            use_source_with_character(
                student,
                seeded_sources[0],
                StudentSourceInteraction.Character.FAHES,
            )
            created_interactions += 1

        return created_sources, created_interactions

        subject = subjects[('الرياضيات', GRADE_LEVEL)]
        source, created = StudentSource.objects.get_or_create(
            user=student,
            title='ملخص تجريبي في الرياضيات',
            defaults={
                'subject': subject,
                'description': 'مصدر نصي تجريبي يوضح كيف تتعامل شخصيات برّاق مع مصادر الطالب.',
                'source_type': StudentSource.SourceType.TEXT,
                'original_filename': 'demo_math_summary.txt',
                'file_size': 0,
                'mime_type': 'text/plain',
                'extension': 'txt',
                'status': StudentSource.Status.READY,
                'extracted_text': (
                    'ملخص في التفاضل: مشتقة x^2 هي 2x. '
                    'النهايات تساعد على فهم سلوك الدالة قرب نقطة محددة. '
                    'عند حل مسائل التفاضل يجب تحديد القاعدة المناسبة ثم التحقق من الناتج.'
                ),
                'metadata': {'demo_seed': True},
            },
        )
        if created or not source.file:
            content = ContentFile(
                (
                    'ملخص في التفاضل\n'
                    'مشتقة x^2 هي 2x.\n'
                    'النهايات تساعد على فهم سلوك الدالة قرب نقطة محددة.\n'
                    'ابدأ بتحديد القاعدة المناسبة ثم تحقق من الناتج.\n'
                ).encode(),
                name='demo_math_summary.txt',
            )
            source.file.save('demo_math_summary.txt', content, save=False)
        source.subject = subject
        source.file_size = source.file.size if source.file else source.file_size
        source.status = StudentSource.Status.READY
        source.save()
        self._write_upsert('student source', source.title, created)

        interactions_before = StudentSourceInteraction.objects.filter(
            user=student,
            source=source,
        ).count()
        for character in (
            StudentSourceInteraction.Character.RASHEED,
            StudentSourceInteraction.Character.KHOTA,
            StudentSourceInteraction.Character.FAHES,
        ):
            if not StudentSourceInteraction.objects.filter(
                user=student,
                source=source,
                character=character,
            ).exists():
                use_source_with_character(student, source, character)

        interactions_after = StudentSourceInteraction.objects.filter(
            user=student,
            source=source,
        ).count()
        return 1, interactions_after - interactions_before

    def _write_upsert(self, kind, label, created):
        action = 'created' if created else 'updated'
        self.stdout.write(f'{action}: {kind} - {self._safe_output(label)}')

    def _safe_output(self, value):
        text = str(value)
        encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
        return text.encode(encoding, errors='backslashreplace').decode(encoding)
