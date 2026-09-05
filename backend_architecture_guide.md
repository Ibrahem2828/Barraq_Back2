# دليل هندسة Backend لمشروع برّاق

## 1. نظرة عامة

هذا المشروع هو Backend لمنصة **برّاق** التعليمية، ومبني باستخدام:

- Python
- Django
- Django REST Framework
- JWT Authentication عبر Simple JWT
- PostgreSQL في بيئة الإنتاج وDocker
- Swagger / OpenAPI عبر drf-spectacular

المشروع وصل حاليًا إلى **Phase 3.6**، ويغطي:

- Phase 1: الهوية والمستخدمين والملف الدراسي والمراحل والمواد
- Phase 2: التخطيط الدراسي "خُطى"
- Phase 3: الاختبارات والتقييم "فاحص"
- Phase 3.5: health/meta/seed data/response helpers/exception handler/regression tests
- Phase 3.6: AI Gateway configuration + status + health + mock services

الهدف من هذا الدليل هو وصف المشروع **من الداخل**: ما هي بنيته، ما وظيفة كل مجلد، وما وظيفة كل ملف مهم، وكيف تتحرك البيانات بين الطبقات المختلفة.

---

## 2. البنية العامة للمشروع

المشروع منظم بأسلوب Django تقليدي مع فصل واضح بين:

- **config**: إعدادات المشروع والـ URL root
- **apps/common**: المكونات المشتركة بين جميع التطبيقات
- **apps/users**: نظام المستخدمين والمصادقة
- **apps/students**: الملف الدراسي للطالب
- **apps/subjects**: المراحل والمواد وربط المستخدم بمواده
- **apps/study_plans**: نظام التخطيط الدراسي
- **apps/quizzes**: نظام الاختبارات والمحاولات والنتائج
- **apps/ai_integration**: البوابة الفعلية لخدمة الذكاء الاصطناعي المستقلة (jobs، webhooks، manifests)
- **apps/analytics / audio / summaries / notifications / support**: تطبيقات فعالة (توصيات، تفريغ صوتي، ملخصات، إشعارات، دعم)

الفكرة الأساسية في التصميم هي:

- **الموديلات** تحفظ البيانات والعلاقات والقيود
- **الـ serializers** تمثل API contract والتحقق من المدخلات
- **الـ views / viewsets** تدير حركة HTTP فقط
- **services.py** يحتوي منطق الأعمال الفعلي
- **selectors.py** يحتوي استعلامات القراءة المتكررة والمحسّنة
- **permissions.py** يعزل الوصول بين المستخدمين
- **admin.py** يجهّز إدارة النظام عبر Django Admin
- **tests.py** يثبت أن السلوك المطلوب يعمل ولا ينكسر

---

## 3. ملفات الجذر Root Files

### `manage.py`
- نقطة تشغيل أوامر Django.
- يستخدم لتشغيل السيرفر، المهاجرات، الاختبارات، والأوامر الإدارية مثل `createsuperuser`.

### `requirements.txt`
- يحتوي dependencies الأساسية للمشروع.
- يمثل العقد البرمجي لبيئة Python المطلوبة لتشغيل النظام.

### `.env.example`
- نموذج لمتغيرات البيئة.
- يوضح القيم المطلوبة مثل مفاتيح Django، قواعد البيانات، CORS، وإعدادات AI Gateway.

### `.gitignore`
- يحدد الملفات التي لا يجب تتبعها في Git.
- يحمي المستودع من إدخال ملفات البيئة، قواعد بيانات محلية، وملفات كاش غير مهمة.

### `Dockerfile`
- يبني image لتشغيل Django داخل حاوية.
- مهم لبيئات الاختبار والتشغيل الموحدة.

### `docker-compose.yml`
- يعرّف الخدمات الأساسية للمشروع.
- عادة يشمل:
  - `web`
  - `db`
  - وأحيانًا `redis` كتمهيد للتوسع القادم

### `README.md`
- الوثيقة التشغيلية الأساسية للمشروع.
- يشرح طريقة التشغيل، الـ endpoints، المراحل المنفذة، وأوامر الاختبارات.

### `frontend_api_contract.json`
- ملف JSON مرجعي للفرونت يصف الـ APIs الحالية بعد Phase 3.6.
- يستخدمه فريق React Native ولوحة التحكم في الربط المباشر.

### `frontend_api_contract_summary.md`
- نسخة مختصرة بشرية للـ API contract.
- مناسبة للقراءة السريعة بدل مراجعة JSON الكبير.

### `db.sqlite3`
- قاعدة بيانات محلية للتطوير فقط.
- ليست التصميم المستهدف للإنتاج، لكنها مفيدة للتشغيل السريع محليًا.

### `quizzes_validation.sqlite3`
- قاعدة بيانات محلية إضافية استخدمت في التحقق العملي أو smoke/regression الخاصة بجزء الاختبارات.
- ليست جزءًا من بنية الإنتاج نفسها.

---

## 4. مجلد `config/`

هذا المجلد هو قلب مشروع Django من ناحية الإعداد والتوجيه العام.

### `config/__init__.py`
- يعرّف الحزمة فقط.
- لا يحتوي منطقًا وظيفيًا بذاته.

### `config/settings.py`
- ملف الإعدادات الرئيسي.
- يحتوي:
  - `INSTALLED_APPS`
  - إعدادات قاعدة البيانات
  - `AUTH_USER_MODEL = 'users.User'`
  - إعدادات DRF
  - إعدادات JWT
  - إعدادات Swagger
  - CORS
  - متغيرات AI Gateway
  - `APP_FEATURES` لتوصيف ما هو مفعّل في النسخة الحالية

وظيفته المعمارية:
- تجميع كل إعدادات المشروع في نقطة واحدة
- ربط المشروع بالتطبيقات الفعلية
- فرض طبقة موحدة للمصادقة والـ pagination والـ exception handling

### `config/urls.py`
- نقطة تجميع الـ routes العامة للمشروع.
- يربط:
  - system endpoints
  - auth/users
  - students
  - subjects
  - study plans
  - quizzes
  - ai gateway
  - schema/docs

وظيفته المعمارية:
- الحفاظ على نقطة دخول موحدة لكل API
- منع تشتت المسارات بين التطبيقات

### `config/asgi.py`
- نقطة تشغيل ASGI.
- مهمة في البيئات التي تستخدم async servers أو أي توسع مستقبلي غير متزامن.

### `config/wsgi.py`
- نقطة تشغيل WSGI التقليدية.
- تستخدم مع Gunicorn أو تشغيل Django التقليدي في الإنتاج.

---

## 5. مجلد `apps/` ككل

هذا المجلد يحتوي التطبيقات الدومينية الحقيقية للنظام.

### `apps/__init__.py`
- يعرّف مجلد `apps` كحزمة Python.

---

## 6. التطبيق المشترك `apps/common/`

هذا التطبيق ليس domain business بحد ذاته، بل يوفر البنية التحتية المشتركة التي تعتمد عليها بقية التطبيقات.

### `apps/common/__init__.py`
- تعريف الحزمة.

### `apps/common/apps.py`
- إعداد Django AppConfig للتطبيق المشترك.

### `apps/common/models.py`
- يحتوي `BaseModel`.
- `BaseModel` يضيف:
  - `created_at`
  - `updated_at`

أهميته:
- توحيد الطوابع الزمنية عبر أغلب موديلات المشروع.
- تقليل تكرار الكود.

### `apps/common/pagination.py`
- يحتوي `StandardResultsSetPagination`.
- يحدد سلوك pagination الموحد في النظام.
- يدعم `page` و`page_size` مع حد أعلى.

أهميته:
- إعطاء الفرونت سلوكًا ثابتًا في جميع الـ list endpoints.

### `apps/common/responses.py`
- يحتوي response helpers:
  - `success_response`
  - `error_response`
  - `validation_error_response`

أهميته:
- يستخدم خصوصًا في system endpoints وبعض الـ service/status endpoints.
- يعطي شكل envelope واضح للردود عندما يكون ذلك مطلوبًا.

### `apps/common/exceptions.py`
- يحتوي `custom_exception_handler`.
- يحول كثيرًا من أخطاء DRF إلى صيغة خطأ موحدة فيها:
  - `success`
  - `message`
  - `errors`
  - `code`

أهميته:
- تقليل الفوضى في رسائل الخطأ على الفرونت.
- جعل سلوك الأخطاء متماسكًا عبر النظام.

### `apps/common/permissions.py`
- يحتوي permissions مشتركة مثل:
  - `IsStaffRole`
  - `IsSuperAdmin`
  - `ReadOnlyOrStaffRole`

أهميته:
- مركزية الصلاحيات العامة بدل تكرارها في كل تطبيق.

### `apps/common/views.py`
- يحتوي:
  - `HealthCheckView`
  - `ProjectMetaView`

وظيفته:
- توفير endpoints تشغيلية على مستوى المشروع:
  - `GET /api/health/`
  - `GET /api/meta/`

### `apps/common/urls.py`
- يربط system endpoints الخاصة بـ health وmeta.

### `apps/common/admin.py`
- ملف الإدارة العامة المشتركة.
- مكان طبيعي لأي تسجيلات أو توسيعات إدارية عامة مستقبلًا.

### `apps/common/tests.py`
- يحتوي اختبارات regression وبنية النظام العامة، مثل:
  - عمل health
  - عمل meta
  - نجاح seed data
  - عزل بيانات المستخدمين
  - استمرار schema/docs

### `apps/common/migrations/__init__.py`
- يعرّف حزمة migrations للتطبيق.

---

## 7. تطبيق المستخدمين `apps/users/`

هذا التطبيق هو الأساس الأمني والهويوي للنظام كله.

### `apps/users/__init__.py`
- تعريف الحزمة.

### `apps/users/apps.py`
- إعداد AppConfig لتطبيق المستخدمين.

### `apps/users/models.py`
- يحتوي `User` custom model.
- يعتمد على:
  - `AbstractBaseUser`
  - `PermissionsMixin`
  - `BaseModel`

الخصائص الأساسية:
- البريد الإلكتروني هو `USERNAME_FIELD`
- الأدوار الحالية:
  - `student`
  - `admin`
  - `support`
  - `super_admin`

أهميته:
- يضمن أن كل أجزاء النظام مبنية على نموذج مستخدم مخصص وقابل للتوسع.

### `apps/users/managers.py`
- يحتوي `UserManager`.
- مسؤول عن:
  - `create_user`
  - `create_superuser`

أهميته:
- يضمن إنشاء المستخدمين بطريقة صحيحة ومتوافقة مع model المخصص.

### `apps/users/forms.py`
- يحتوي forms الخاصة بـ Django Admin:
  - `CustomUserCreationForm`
  - `CustomUserChangeForm`

أهميته:
- يجعل إدارة المستخدمين من لوحة الإدارة متوافقة مع الـ custom user model.

### `apps/users/serializers.py`
- يحتوي serializers الخاصة بالمستخدمين والمصادقة:
  - `UserSerializer`
  - `RegisterSerializer`
  - `CustomTokenObtainPairSerializer`

وظائفها:
- قراءة بيانات المستخدم
- تسجيل مستخدم جديد
- توسيع رد login ليشمل الـ JWT وبيانات المستخدم

### `apps/users/views.py`
- يحتوي:
  - `RegisterView`
  - `LoginView`
  - `UserMeView`

وظيفته:
- تنفيذ endpoints:
  - `POST /api/auth/register/`
  - `POST /api/auth/login/`
  - `POST /api/auth/refresh/` عبر Simple JWT
  - `GET/PATCH /api/users/me/`

### `apps/users/permissions.py`
- يحتوي permission مثل `IsAccountOwner`.
- يستخدم لحماية بيانات المستخدم من الوصول غير المصرح.

### `apps/users/urls.py`
- يربط مسارات auth وme الخاصة بالمستخدم الحالي.

### `apps/users/admin.py`
- يجهز User Admin متوافقًا مع الـ custom user model.
- يحسن العرض والبحث والتصفية داخل لوحة الإدارة.

### `apps/users/tests.py`
- يحتوي اختبارات التطبيق الخاصة بالمستخدمين والمصادقة.

### `apps/users/migrations/0001_initial.py`
- migration الأولية لإنشاء custom user model.

### `apps/users/migrations/__init__.py`
- تعريف حزمة migrations.

---

## 8. تطبيق الملف الدراسي `apps/students/`

هذا التطبيق يضيف بعدًا أكاديميًا للمستخدم الذي يكون في الأصل مجرد حساب.

### `apps/students/__init__.py`
- تعريف الحزمة.

### `apps/students/apps.py`
- إعداد AppConfig للتطبيق.

### `apps/students/models.py`
- يحتوي `StudentProfile`.
- يربط المستخدم بمعلوماته الدراسية مثل:
  - المرحلة
  - الصف
  - التخصص
  - الهدف الدراسي
  - الساعات اليومية
  - اكتمال الإعداد الأولي

أهميته:
- يجعل بقية الأنظمة، مثل التخطيط والاختبارات، تعتمد على ملف دراسي فعلي.

### `apps/students/serializers.py`
- يحتوي:
  - `StudentProfileSerializer`
  - `StudentProfileSetupSerializer`

أهميته:
- يفصل بين قراءة/تعديل الملف وبين إعداد الملف الأولي.

### `apps/students/views.py`
- يحتوي:
  - `StudentProfileSetupView`
  - `StudentProfileDetailView`

وظيفته:
- endpoints الخاصة بإنشاء وتحديث ملف الطالب:
  - `POST /api/students/setup-profile/`
  - `GET /api/students/profile/`
  - `PATCH /api/students/profile/`

### `apps/students/urls.py`
- يربط مسارات student profile.

### `apps/students/admin.py`
- يسجل `StudentProfile` في Django Admin.

### `apps/students/tests.py`
- يحتوي اختبارات الطالب والملف الدراسي.

### `apps/students/migrations/0001_initial.py`
### `apps/students/migrations/0002_initial.py`
### `apps/students/migrations/0003_initial.py`
- تحفظ تطور مخطط قاعدة البيانات الخاص بالملف الدراسي.

### `apps/students/migrations/__init__.py`
- تعريف حزمة migrations.

---

## 9. تطبيق المواد والمراحل `apps/subjects/`

هذا التطبيق هو المرجع الأكاديمي الذي تعتمد عليه الخطة الدراسية والاختبارات.

### `apps/subjects/__init__.py`
- تعريف الحزمة.

### `apps/subjects/apps.py`
- إعداد AppConfig.

### `apps/subjects/models.py`
- يحتوي:
  - `EducationStage`
  - `Subject`
  - `UserSubject`

وظائفها:
- تعريف المراحل الدراسية
- تعريف المواد المرتبطة بالمراحل
- ربط المستخدم بمواده المختارة

### `apps/subjects/serializers.py`
- يحتوي:
  - `EducationStageSerializer`
  - `SubjectSerializer`
  - `UserSubjectWriteSerializer`
  - `UserSubjectReadSerializer`

أهميته:
- يفصل بين serializer الكتابة والقراءة في ربط المستخدم بالمواد.

### `apps/subjects/views.py`
- يحتوي:
  - `EducationStageListView`
  - `SubjectListView`
  - `UserSubjectListCreateView`
  - `UserSubjectDestroyView`

وظيفته:
- توفير مرجع المواد والمراحل
- السماح للمستخدم بإدارة المواد التي يتابعها

### `apps/subjects/urls.py`
- يربط endpoints الخاصة بالمراحل والمواد ومواد المستخدم.

### `apps/subjects/admin.py`
- يسجل `EducationStage`, `Subject`, `UserSubject` في Django Admin مع بحث وفلاتر واضحة.

### `apps/subjects/tests.py`
- يختبر filtering والربط بمواد المستخدم وصحة المسارات.

### `apps/subjects/management/__init__.py`
- تعريف الحزمة الخاصة بالأوامر الإدارية.

### `apps/subjects/management/commands/__init__.py`
- تعريف حزمة أوامر management commands.

### `apps/subjects/management/commands/seed_academic_data.py`
- management command لتغذية قاعدة البيانات بالمراحل والمواد الأولية.

أهميته:
- يحل مشكلة البيئة الفارغة بسرعة.
- يوفر baseline ثابتًا للتجارب والاختبارات والفرونت.

### `apps/subjects/migrations/0001_initial.py`
### `apps/subjects/migrations/0002_initial.py`
- تحفظ إنشاء وتطور الجداول الخاصة بالمراحل والمواد.

### `apps/subjects/migrations/__init__.py`
- تعريف حزمة migrations.

---

## 10. تطبيق التخطيط الدراسي `apps/study_plans/`

هذا التطبيق ينفذ نظام **خُطى**، وهو محرك التخطيط الدراسي داخل برّاق.

### `apps/study_plans/__init__.py`
- تعريف الحزمة.

### `apps/study_plans/apps.py`
- إعداد AppConfig.

### `apps/study_plans/models.py`
- يحتوي:
  - `StudyPlan`
  - `StudyTask`
  - `StudyPlanProgressLog`

وظائفها:
- `StudyPlan`: الخطة الرئيسية للمستخدم
- `StudyTask`: المهام اليومية/الجزئية التابعة للخطة
- `StudyPlanProgressLog`: سجل أحداث التقدم لأغراض التتبع والتحليلات المستقبلية

أهميته:
- يترجم هدف الطالب إلى خطة قابلة للتنفيذ.

### `apps/study_plans/serializers.py`
- يحتوي serializers متعددة ومتخصصة، مثل:
  - `StudyPlanListSerializer`
  - `StudyPlanDetailSerializer`
  - `StudyPlanCreateSerializer`
  - `StudyPlanUpdateSerializer`
  - `StudyTaskSerializer`
  - `StudyTaskCreateSerializer`
  - `StudyTaskUpdateSerializer`
  - `TaskStatusUpdateSerializer`
  - serializers لردود today/week

أهميته:
- يفصل بين:
  - العرض المختصر
  - العرض التفصيلي
  - الإنشاء
  - التحديث
  - استجابات التجميع الزمني

### `apps/study_plans/services.py`
- أهم ملف أعمال في هذا التطبيق.
- يحتوي منطق مثل:
  - إنشاء خطة manual
  - إنشاء خطة AI عبر mock/gateway
  - توليد المهام
  - حساب نسبة الإنجاز
  - إضافة مهمة
  - تحديث مهمة
  - إكمال/تخطي/إعادة فتح مهمة

أهميته:
- يمنع تضخم الـ views
- يجعل منطق الأعمال قابلًا للاختبار وإعادة الاستخدام

### `apps/study_plans/selectors.py`
- يحتوي استعلامات محسّنة للقراءة، مثل:
  - استعلام خطط المستخدم
  - استعلام مهام اليوم
  - استعلام مهام الأسبوع

أهميته:
- تحسين الأداء عبر `select_related` و`prefetch_related`
- فصل منطق القراءة عن منطق التعديل

### `apps/study_plans/permissions.py`
- يحتوي `IsStudyPlanOwner`.
- يضمن أن المستخدم لا يصل إلا إلى خططه ومهامه هو.

### `apps/study_plans/views.py`
- يحتوي:
  - `StudyPlanViewSet`
  - `StudyTaskViewSet`

الوظيفة:
- يربط طبقة HTTP بالخدمات.
- يوفر:
  - CRUD للخطط
  - CRUD للمهام الأساسية
  - endpoints المخصصة مثل:
    - today
    - week
    - complete
    - skip
    - reopen

### `apps/study_plans/urls.py`
- يستخدم `SimpleRouter`.
- يسجل:
  - `study-plans`
  - `study-tasks`

### `apps/study_plans/admin.py`
- يسجل:
  - `StudyPlan`
  - `StudyTask`
  - `StudyPlanProgressLog`
- ويعرض `StudyTask` كـ inline داخل `StudyPlan`.

### `apps/study_plans/tests.py`
- يحتوي اختبارات عملية تغطي:
  - إنشاء الخطة
  - توليد المهام
  - حماية الملكية
  - today/week
  - حساب الإنجاز
  - schema stability

### `apps/study_plans/migrations/0001_initial.py`
- migration التأسيسية لنظام خُطى.

### `apps/study_plans/migrations/__init__.py`
- تعريف حزمة migrations.

---

## 11. تطبيق الاختبارات `apps/quizzes/`

هذا التطبيق ينفذ نظام **فاحص**، وهو محرك التقييم والاختبارات داخل برّاق.

### `apps/quizzes/__init__.py`
- تعريف الحزمة.

### `apps/quizzes/apps.py`
- إعداد AppConfig.

### `apps/quizzes/models.py`
- يحتوي:
  - `Quiz`
  - `Question`
  - `Choice`
  - `QuizAttempt`
  - `StudentAnswer`
  - `QuestionBankItem`
  - `QuizProgressLog`

وظائفها:
- `Quiz`: الاختبار الرئيسي
- `Question`: سؤال تابع للاختبار
- `Choice`: خيارات السؤال
- `QuizAttempt`: محاولة الطالب
- `StudentAnswer`: إجابة الطالب الفعلية
- `QuestionBankItem`: نواة بنك الأسئلة المستقبلي
- `QuizProgressLog`: سجل أحداث التقييم

### `apps/quizzes/serializers.py`
- يحتوي serializers كثيرة لأن التطبيق يتعامل مع أكثر من حالة عرض:
  - عرض quiz
  - عرض question أثناء الحل
  - عرض question بعد النتيجة
  - إنشاء quiz
  - تحديث quiz
  - بدء attempt
  - إرسال إجابة واحدة
  - تسليم الاختبار كاملًا
  - عرض النتيجة
  - عرض question bank

أهميته:
- يمنع كشف `is_correct` أثناء الحل
- يغير شكل البيانات بحسب حالة الـ attempt

### `apps/quizzes/services.py`
- الملف الأهم في منطق فاحص.
- يحتوي منطق مثل:
  - إنشاء اختبار
  - توليد أسئلة mock
  - AI quiz mock generation
  - بدء محاولة
  - حفظ إجابة واحدة
  - حساب النتيجة
  - تسليم المحاولة
  - بناء التوصيات
  - أرشفة الاختبار
  - ترك المحاولة

أهميته:
- يعزل منطق التصحيح والحساب عن الـ views
- يجعل النظام قابلًا للتوسع لاحقًا عند إضافة بنك أسئلة أو AI حقيقي

### `apps/quizzes/selectors.py`
- يحتوي استعلامات محسنة مثل:
  - quizzes الخاصة بالمستخدم
  - quiz detail
  - attempts الخاصة بالمستخدم
  - attempt detail
  - question bank items

### `apps/quizzes/permissions.py`
- يحتوي:
  - `IsQuizOwner`
  - `CanAccessQuestionBankItem`

أهميته:
- يعزل الاختبارات والمحاولات بحسب مالكها
- يسمح بعرض الأسئلة العامة أو التي تخص المستخدم داخل question bank

### `apps/quizzes/views.py`
- يحتوي:
  - `QuizViewSet`
  - `QuizAttemptViewSet`
  - `QuestionBankViewSet`

الوظائف:
- إدارة quizzes
- بدء المحاولات
- حفظ الإجابات
- تسليم الاختبار
- جلب النتيجة
- جلب question bank

### `apps/quizzes/urls.py`
- يستخدم `SimpleRouter`.
- يسجل:
  - `quizzes`
  - `quiz-attempts`
  - `question-bank`

### `apps/quizzes/admin.py`
- يسجل جميع موديلات التقييم في Django Admin:
  - `Quiz`
  - `Question`
  - `Choice`
  - `QuizAttempt`
  - `StudentAnswer`
  - `QuestionBankItem`
  - `QuizProgressLog`

### `apps/quizzes/tests.py`
- يحتوي اختبارات endpoint وسلوك شامل، مثل:
  - إنشاء quiz
  - حماية الإجابات الصحيحة قبل التسليم
  - بدء attempt
  - submit
  - result
  - unanswered count
  - العزل بين المستخدمين
  - question bank
  - abandon
  - schema stability

### `apps/quizzes/migrations/0001_initial.py`
- migration التأسيسية لتطبيق الاختبارات.

### `apps/quizzes/migrations/__init__.py`
- تعريف حزمة migrations.

---

## 13. التطبيقات المستقبلية Placeholder Apps

هذه التطبيقات موجودة كبنية تحضيرية فقط، وليست منفذة فعليًا بعد.

### `apps/analytics/__init__.py`
- placeholder لتحليلات الأداء والسلوك.

### `apps/audio/__init__.py`
- placeholder للميزات المتعلقة بالصوت.

### `apps/summaries/__init__.py`
- placeholder لخدمات التلخيص.

### `apps/notifications/__init__.py`
- placeholder للتنبيهات والإشعارات.

### `apps/support/__init__.py`
- placeholder للدعم والمساعدة والتذاكر.

وجود هذه التطبيقات الآن مهم معماريًا لأنه:
- يوضح اتجاه التوسع القادم
- يمنع الفوضى عند إضافة مراحل جديدة
- يجعل أسماء التطبيقات والمسارات المستقبلية محسومة مبكرًا

---

## 14. كيف تتحرك الطلبات داخل النظام

سير الطلب النموذجي في هذا المشروع يكون كالتالي:

1. الطلب يدخل من `config/urls.py`
2. يتم توجيهه إلى `urls.py` الخاص بالتطبيق
3. يصل إلى `view` أو `viewset`
4. الـ view يطبّق المصادقة والصلاحيات ويختار serializer مناسب
5. الـ serializer يتحقق من المدخلات
6. منطق الأعمال الحقيقي يُنفذ غالبًا في `services.py`
7. القراءة المحسنة تتم عبر `selectors.py` عندما يلزم
8. النتيجة تُعاد إما:
   - raw DRF serializer response
   - أو enveloped response في system/service endpoints
9. في حال الخطأ، يمر جزء كبير من الأخطاء عبر `custom_exception_handler`

هذا التقسيم جيد لأنه:
- يقلل ازدحام الـ views
- يسهل الاختبارات
- يدعم التوسع دون كسر العقود الحالية

---

## 15. نمط المصادقة والأمان

المشروع يعتمد:

- **JWT Bearer Authentication**
- custom user model
- object-level isolation في التطبيقات الحساسة

المسار الأمني الأساسي:

- التسجيل عبر `register`
- الحصول على `access` و`refresh` عبر `login`
- تمرير:
  - `Authorization: Bearer <access_token>`

العزل الحالي مهم جدًا:

- المستخدم لا يرى `StudyPlan` أو `StudyTask` لمستخدم آخر
- المستخدم لا يرى `Quiz` أو `QuizAttempt` أو `StudentAnswer` لمستخدم آخر
- `QuestionBank` يعرض فقط:
  - الأسئلة العامة
  - أو الأسئلة التي يملكها المستخدم

---

## 16. إدارة الـ API Contracts

المشروع يملك الآن طبقتين توثيقيتين:

### Swagger / OpenAPI
- `GET /api/schema/`
- `GET /api/docs/`

هذه مناسبة لـ:
- الاستكشاف السريع
- الاختبارات اليدوية
- التحقق من الـ schema

### ملفات التوثيق الخاصة بالفرونت
- `frontend_api_contract.json`
- `frontend_api_contract_summary.md`

هذه مناسبة لـ:
- الربط العملي مع React Native
- تحديد request/response fields بدقة
- مشاركة العقد مع فرق الواجهة ولوحة التحكم

---

## 17. أسلوب بناء الـ APIs في المشروع

المشروع لا يستخدم نمطًا واحدًا فقط، بل يستخدم ما يناسب كل حالة:

- `APIView` للنقاط النظامية أو endpoints الصغيرة الواضحة
- `generics` في endpoints CRUD البسيطة
- `ViewSet` مع `SimpleRouter` في الأنظمة الدومينية الأكبر مثل:
  - study_plans
  - quizzes

هذا الاختيار منطقي لأن:
- CRUD المعقد يحتاج actions متعددة
- النظاميات الصغيرة لا تحتاج Router ثقيل

---

## 18. نمط الاختبارات

كل تطبيق رئيسي يحتوي `tests.py`.

الاختبارات الحالية تغطي:

- المصادقة
- العزل بين المستخدمين
- صحة الـ endpoints
- استمرارية Swagger/schema
- سلوك الخطة الدراسية
- سلوك الاختبارات والتصحيح
- جاهزية AI Gateway mock mode
- seed data
- regression الأساسية

هذا يعني أن المشروع ليس مجرد prototype، بل فيه حد واضح من الاعتمادية التشغيلية.

---

## 19. ما الذي تم بناؤه فعليًا حتى الآن

### على مستوى Phase 1
- نظام مستخدمين مخصص
- تسجيل ودخول وJWT
- ملف دراسي
- مراحل ومواد
- Swagger
- Docker foundation

### على مستوى Phase 2
- نظام خُطى:
  - خطط
  - مهام
  - خطة اليوم
  - خطة الأسبوع
  - progress logs

### على مستوى Phase 3
- نظام فاحص:
  - quizzes
  - questions
  - choices
  - attempts
  - answers
  - results
  - question bank

### على مستوى Phase 3.5
- health
- meta
- response helpers
- exception handler
- seed data
- regression tests

### على مستوى Phase 3.6
- AI Gateway config
- AI Gateway status
- AI Gateway health
- mock/remote-ready AI service layer

---

## 20. ما الذي لم يُنفذ بعد

هذه النقاط ليست ناقصة بالخطأ، بل مؤجلة عمدًا:

- analytics الحقيقي
- audio الحقيقي
- summaries الحقيقي
- notifications
- support workflows
- AI production integration الحقيقي

السبب المعماري الصحيح:
- لا يجب خلط مراحل التوسع القادمة مع نواة المنتج الحالية
- البنية الآن جاهزة لتلقي هذه الوحدات دون إعادة بناء كبيرة

---

## 21. التقييم المعماري للمشروع

من منظور هندسي، المشروع الحالي يمتلك نقاط قوة واضحة:

- custom user model من البداية
- فصل جيد بين domain apps
- استخدام `services.py` في الأجزاء الثقيلة
- استخدام `selectors.py` لتحسين الاستعلامات
- isolation جيد للبيانات
- توثيق API جيد عبر Swagger وcontract JSON
- اختبارات regression حقيقية
- AI gateway كطبقة وسيطة بدل ربط مباشر غير منظم

أما أهم نقاط التوسع الطبيعية لاحقًا فهي:

- إضافة background jobs عند نمو AI أو الإشعارات
- إدخال caching حيث يلزم
- فصل إعدادات dev/staging/prod إذا زاد تعقيد التشغيل
- بناء analytics layer فوق logs الحالية بدل البدء من الصفر

---

## 22. متى يُستخدم هذا الملف

هذا الملف مناسب للجهات التالية:

- مطور Backend جديد يدخل على المشروع
- مطور Frontend يريد فهم من أين تأتي البيانات
- مهندس QA يريد فهم المسؤوليات والطبقات
- Product/Tech Lead يريد رؤية الصورة الكاملة

إذا كان المطلوب:

- **فهم المسارات والـ payloads**: ابدأ من `frontend_api_contract.json`
- **فهم كيفية تشغيل المشروع**: ابدأ من `README.md`
- **فهم كيف بُني النظام داخليًا**: ابدأ من هذا الملف

---

## 23. خلاصة تنفيذية

المشروع الحالي ليس مجرد مجموعة endpoints، بل منصة Backend منظمة تتكون من:

- نواة هوية ومستخدمين مستقرة
- طبقة أكاديمية واضحة
- طبقة تخطيط دراسي
- طبقة اختبارات وتقييم
- طبقة مشتركة للبنية والردود والأخطاء
- طبقة AI Gateway جاهزة للتبديل مستقبلًا

بمعنى عملي:
- الحساب يعرّف المستخدم
- الملف الدراسي يحدد سياقه الأكاديمي
- المواد تحدد نطاق التعلم
- خُطى يبني خطة التنفيذ
- فاحص يقيس التقدم والفهم
- common يفرض التماسك التشغيلي
- ai_integration هو البوابة الفعلية المفعّلة لخدمة الذكاء الاصطناعي المستقلة

هذا يجعل المشروع في وضع جيد جدًا للاستمرار إلى المراحل القادمة بدون الحاجة إلى إعادة هندسة جذرية.
