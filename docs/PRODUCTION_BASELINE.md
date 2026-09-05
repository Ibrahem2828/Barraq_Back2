# خط الأساس للإنتاج — Baraq Django Backend

**التاريخ:** 2026-07-26  
**النطاق:** Phase 0 فقط — Inventory وBaseline. لا يتضمن هذا المستند إصلاحات أو ادعاء جاهزية.  
**المستودع/المرجع:** `main` عند `413c9fe`  
**حالة مساحة العمل عند القياس:** غير نظيفة. كانت هناك تعديلات غير ملتزمة مسبقاً في `apps/ai_integration` و`apps/sources` و`config/settings.py` واختبارات و`scripts/validate_release.py`، وملفان جديدان هما `apps/common/test_runner.py` و`docs/BACKEND_PRODUCTION_READINESS_AUDIT_AR.md`، وحذف متتبع للملف `db.sqlite3-journal`. لم تُعدّل هذه المرحلة أي كود تشغيلي؛ الملف الحالي هو مخرج Phase 0 المطلوب.

## 1. القرار عند خط الأساس

**الحالة: BLOCKED.**

المشروع يملك أساساً جيداً (Django 6، اختبارات حالية ناجحة، Docker متعدد المراحل، وعدم وجود drift في migrations)، لكنه لا يطابق بعد عقد التكامل والأمان والتشغيل المطلوب للإنتاج. أهم العوائق هي HMAC غير مكتمل، غياب الـOutbox/Saga الدائم، عدم وجود ربط manifest/download بـAIJob، فشل جودة OpenAPI، والافتقار إلى أدلة PostgreSQL/Redis/Celery/S3 وتشغيل الحاويات الفعلي.

## 2. جرد المشروع

### التطبيقات

| الحالة | التطبيقات |
|---|---|
| مثبّتة ونشطة | `common`, `users`, `students`, `subjects`, `study_plans`, `quizzes`, `sources`, `subscriptions`, `ai_integration`, `analytics`, `summaries`, `audio`, `notifications`, `support`, `admin_dashboard` |
| موجودة لكن غير مثبّتة | `ai_gateway`, `ai_platform` |
| حزمة طرف ثالث ذات أثر معماري | Django REST Framework، SimpleJWT + blacklist، drf-spectacular، Celery، Redis cache، WhiteNoise، CORS |

يوجد 17 مجلداً لتطبيقات المشروع. الاختبارات موجودة حالياً في: `admin_dashboard`, `ai_gateway`, `ai_integration`, `ai_platform`, `common`, `quizzes`, `sources`, `students`, `study_plans`, `subjects`, `subscriptions`, `users`. لا توجد ملفات اختبارات مخصصة حالياً لبعض التطبيقات النشطة مثل `analytics`, `audio`, `notifications`, `summaries`, `support`.

### التطبيقات القديمة للذكاء الاصطناعي

`apps.ai_gateway` و`apps.ai_platform` غير موجودين في `INSTALLED_APPS` وغير مشمولين من URLconf النشط. تحتوي الشجرة القديمة على TODO/mock logic وطبقة provider/prompt/RAG. لا يجوز تفعيلهما. لا تزال إزالتها أو أرشفتها قراراً لمرحلة 1 بعد اختبار أن لا استيراد تشغيلياً يعتمد عليها.

### الطبقات النشطة

| الطبقة | النتيجة |
|---|---|
| Models | موزعة على التطبيقات أعلاه؛ AI النشط يملك `AIJob`, `AIFeedback`, `AIWebhookEvent` فقط. |
| Serializers / Views | موجودة داخل كل app؛ معظم APIs مبنية على DRF ViewSet/APIView. |
| Services | منطق مهم في `ai_integration.services`, `sources.services`, `subscriptions.services`, وطبقات domain الأخرى. |
| Celery tasks | `ai_integration.dispatch_job`, `forward_feedback`, `cancel_external_job`; `sources.process_source_task`; `users.send_password_reset_email`. مهمة `ai_platform.process_request` قديمة وغير نشطة. |
| Signals | `subscriptions.ensure_subscription_for_new_user` على `post_save(User)`. |
| Middleware | Security, WhiteNoise, Request ID, API version headers, CORS, session, locale, common, CSRF, auth, messages, X-Frame-Options. |

## 3. الواجهات المكتشفة

### الجذور العامة

| المسار | الغرض |
|---|---|
| `/` | Landing page |
| `/admin/` | Django admin |
| `/api/schema/`, `/api/docs/`, `/api/redoc/` | مخطط وواجهات OpenAPI الحالية |
| `/api/v1/` | النسخة الرسمية المعلنة |
| `/api/` | surface قديم متوازٍ يعيد تضمين جميع `config.api_urls` |
| `/api/health/`, `/api/health/live/`, `/api/health/ready/`, `/api/meta/` | Health/meta متاحة خارج `/v1` حالياً |

تم استخراج 542 pattern في URL resolver، يشمل ذلك Django admin. المسار الرسمي `/api/v1/` يغطي: auth، المستخدم/الطالب، المراحل والمواد، الخطط والمهام، الاختبارات والمحاولات وبنك الأسئلة، المصادر والمجموعات، الاشتراكات، AI، التوصيات، الملخصات، التفريغ الصوتي، الإشعارات، الدعم، وواجهة الإدارة البرمجية. تظهر نفس المجموعة أيضاً تحت `/api/`، وهو ازدواج surface عام يجب إنهاؤه لاحقاً بخطة توافق/versioning لا بحذف مفاجئ.

### AI public (الحالة الحالية)

```text
POST /api/v1/ai/jobs/
GET  /api/v1/ai/jobs/{public_id}/
POST /api/v1/ai/jobs/{public_id}/cancel/
POST /api/v1/ai/jobs/{public_id}/feedback/
POST /api/v1/ai/jobs/{public_id}/refresh/        # غير مطلوب في العقد المستهدف
GET  /api/v1/ai/capabilities/
GET  /api/v1/ai/service-health/                  # غير مطلوب في العقد المستهدف
```

المسار المطلوب `GET /api/v1/ai/usage/` غير موجود. يجب توثيق أو ترحيل `refresh` و`service-health` قبل أي تغيير عام.

### AI internal (الحالة الحالية)

```text
POST /api/internal/v1/ai/webhooks/jobs/
GET  /api/internal/v1/ai/sources/{source_id}/manifest/
GET  /api/internal/v1/ai/sources/{source_id}/download/
GET  /api/internal/v1/ai/collections/{collection_id}/manifest/
GET  /api/internal/v1/ai/users/{user_id}/context/
```

هذه المسارات موجودة بالصيغة المطلوبة، لكن تفويضها وعقد توقيعها لا يطابقان بعد المتطلبات المستهدفة.

## 4. نموذج التكامل الحالي مع AI

### الموجود

- `AIJob` يملك `public_id` فريداً، user، character/task type، source أو collection، `external_job_id`، ومؤشرات لحجز/التزام credits.
- إنشاء job يجري داخل transaction ويستدعي حجزاً قائماً على عدادات الاشتراك، ثم يؤجل dispatch بواسطة `transaction.on_commit`.
- العميل الصادر يعطّل redirects ويضع timeouts وتصنيفاً أولياً للأخطاء القابلة لإعادة المحاولة.
- webhook يحفظ `AIWebhookEvent` ثم يستدعي materialization في transaction عند الاكتمال.
- materializers موجودة لشخصيات Fahes/Khota/Rasheed/Kholasa/Sada وتنتج كيانات Django النهائية.

### الفجوات الحرجة

1. التوقيع الحالي يعتمد مفتاحاً داخلياً وHMAC من timestamp/body فقط. لا يحقق canonical contract الذي يشمل `METHOD`, `PATH_WITH_QUERY`, nonce وcontent SHA-256، ولا توجد nonce replay protection في Redis أو service allow-list أو active/previous secret.
2. لا توجد transition map مركزية تمنع انتقالات `AIJob` غير الصالحة؛ توجد تغييرات حالة مباشرة في services/views/tasks.
3. لا توجد النماذج الدائمة المطلوبة `AIJobEvent`, `CreditReservation`, `IntegrationOutbox`, `MaterializationRecord`. `transaction.on_commit` ليس outbox دائماً ويتأثر بانقطاع العامل بعد commit.
4. لا يوجد `output_id` موثق/unique ولا سجل materialization idempotent مستقل.
5. manifest/download لا يستمدان التفويض من AIJob مرجعي ولا يقدمان بعد signed URL قصير العمر مرتبطاً بالمهمة. تمرير `user_id` وحده ليس مرجع ثقة كافياً.
6. نوعا المهمة الحاليان `kholasa_summary` و`sada_transcription` لا يطابقان الاسمين المستهدفين `kholasa_generate_summary` و`sada_transcribe_audio`؛ يلزم compatibility migration موثق.
7. Fahes ينشر الاختبار تلقائياً حالياً؛ يخالف شرط عدم النشر قبل quality policy.
8. لا توجد reconciliation دورية أو خدمة beat مهيأة لتعامل outbox/reservations/jobs العالقة/callbacks.

## 5. المصادر والتخزين

- `StudentSource` وcollections موجودة، مع فحص magic bytes لعدة أنواع، بما فيها الصوت وPDF والصور وOffice ZIP.
- Sada مقيّد حالياً بمصدر صوت واحد ولا يقبل collection، وحد الملفات المعروض 25MB؛ هذه نقطة إيجابية يجب تغطيتها باختبارات إنتاجية.
- checksum يظهر في metadata بعد معالجة المصدر، لكن لا يوجد version أول-درجة للمصدر ضمن العقد.
- لا يوجد في خط الأساس دليل اختبار S3-compatible، signed URL، استرجاع media أو propagation للحذف عبر عدة replicas.

## 6. المصادقة والصلاحيات

- الإعدادات تشمل JWT access/refresh rotation وblacklist.
- User/domain يملك أدواراً وإدارة إدارية منفصلة، وAPI يعزل كثيراً من querysets بحسب المستخدم.
- لم تُثبت في خط الأساس بعد متطلبات RBAC المستهدفة (`student`, `support`, `admin`, `super_admin`, `ai_service`) ولا مصفوفة IDOR كاملة أو MFA readiness أو rate limiting/brute-force evidence.

## 7. التشغيل والبنية

### Docker/Coolify

- يوجد Dockerfile متعدد المراحل على Python 3.12-slim، runtime غير root، `tini`، Gunicorn، وhealthcheck.
- يوجد `docker-compose.yml` بخدمات `migrate`, `web`, `worker`, `db` (PostgreSQL 16), `redis` (7.4). لا توجد خدمة beat.
- المنفذ العام موجود لخدمة `web` فقط على 8000؛ DB/Redis/worker/migrate ليست منشورة علناً في compose.
- compose يتطلب `SECRET_KEY`, `ALLOWED_HOSTS`, `PUBLIC_API_BASE_URL`, `POSTGRES_PASSWORD`, `AI_SERVICE_BASE_URL`, `AI_SERVICE_INTERNAL_API_KEY`, `AI_SERVICE_WEBHOOK_SECRET`.
- `docker compose config --quiet` يفشل بالـ`.env` المحلي كما هو لغياب متغير AI required؛ نجح فحص syntax عند إدخال قيم اختبار مؤقتة، لذلك هذه بوابة إعداد صحيحة وليست دليلاً على تشغيل الحاويات.

### الإعدادات والأمن

- production settings تفرض SECRET_KEY قوياً، hosts، public API HTTPS، Redis، وعناوين CORS/CSRF وحماية JWT وsecurity headers.
- لا يوجد دليل في هذا الخط الأساس على trusted proxy policy، structured JSON logs، Sentry/Prometheus private metrics، HSTS preload المراجع، أو secret-rotation runtime.
- `check --deploy` يخرج 0 لكنه يبلّغ 29 تحذيراً: 28 منها مرتبطة بـdrf-spectacular وواحد `security.W021` لأن `SECURE_HSTS_PRELOAD` غير مفعّل. لا تعد هذه النتيجة قبولاً للإطلاق.

## 8. نتائج أوامر خط الأساس

بيئة العزل استخدمت SQLite ذاكرة وRedis locmem فقط لتقييم بناء Django، وليست بديلاً عن PostgreSQL/Redis production.

| الأمر | النتيجة | الدليل/القيد |
|---|---|---|
| `python -m compileall -q apps config scripts` | PASS | صفر أخطاء syntax. |
| `python scripts/validate_release.py` | PASS | 259 ملف Python، صفر أخطاء حسب فاحص المشروع. |
| `python manage.py check` | PASS في بيئة العزل | التنفيذ المباشر على `.env` المحلي يتوقف عند safety validation لإعداد production غير صالح؛ لم يتم تجاوز الحماية. |
| `python manage.py makemigrations --check --dry-run` | PASS في بيئة العزل | لا تغييرات migrations مقترحة. |
| `python manage.py migrate --plan` | PASS في بيئة العزل | خطة migrations التاريخية تُعرض؛ لم تنفذ migration على DB production. |
| `python manage.py test --verbosity 1` | PASS في بيئة العزل | 125 اختباراً ناجحاً، 0 فاشل، 14.025 ثانية. |
| `python manage.py spectacular --file .baseline_openapi.yaml --validate` | BLOCKED | ينهي process بـ0 لكن يبلّغ **116 errors / 12 warnings**؛ الملف المؤقت أزيل. |
| `python manage.py check --deploy` | BLOCKED كـrelease gate | exit 0 مع 29 تحذيراً مذكورة أعلاه. |
| `ruff check apps config scripts` | NOT RUNNABLE | executable/module `ruff` غير صالح/غير متاح محلياً. |
| `docker compose config --quiet` | PASS بقيم اختبار مؤقتة | لا يثبت build أو startup؛ فشل مع `.env` المحلي الناقص كما يجب. |
| `docker compose build --no-cache` | NOT RUN | مؤجل لما بعد إغلاق بوابات Phase 1–8. |
| PostgreSQL/Redis/Celery/S3 integration | NOT RUN | لا توجد بيئة خدمات حقيقية ضمن Phase 0. |
| external health URLs | NO POSITIVE EVIDENCE | المحاولة السابقة انتهت timeout؛ لا يُستنتج منها تعطل الخدمة ولا نجاحها. |

### فشل OpenAPI

drf-spectacular لا يستطيع استنتاج serializers لواجهات APIView كثيرة، منها `APIRootView`, `AuthRootView`, reset/logout/change-password، AI root/capabilities/service-health/webhook/internal manifests/context، subscription root، admin root وproject metadata. توجد أيضاً collisions لأنواع enums (`status`, `character`, `priority`, `category`) وتحذير type hint في `SupportTicketDetailSerializer.get_messages`. لا يحقق ذلك شرط zero project-relevant warnings.

## 9. مسح الجودة والأمان

تم البحث عن TODO/FIXME/pass/NotImplemented، mock logic، broad exceptions، print، أسرار وأوضاع SSL غير آمنة.

| النتيجة | الملاحظة |
|---|---|
| TODO/mock | موجودة في `apps.ai_gateway` القديمة غير المثبّتة. |
| `pass`/`NotImplemented` | موجودة في طبقات legacy `ai_platform` وتحتاج أرشفة/إزالة منظمة، لا تفعيل. |
| broad `except` في المسارات النشطة | موجود في AI tasks/views وبعض services/tasks والصحة والإدارة؛ يحتاج triage واستبدال واضح حسب الخطأ. |
| `print` | موجود في أداة CLI `scripts/validate_release.py`، لا في مسار HTTP حساس مُثبت بهذا المسح. |
| أسرار/credentials صريحة | لم يظهر secret provider واضح في الملفات المتتبعة أو `.env.example` عبر المسح النصي؛ لا يعد ذلك بديلاً عن secret scanner في CI. |
| duplicate routes | يوجد تكرار جوهري لساحة `/api/v1/` تحت `/api/`. |
| duplicate definitions/dead code | لم يثبت فاحص آلي موثوق صفراً؛ legacy AI غير النشط هو أكبر كتلة dead code معروفة. |
| `git diff --check` | PASS؛ لا أخطاء whitespace في التغييرات القائمة. |

## 10. مخاطر مصنفة

### P0 — تمنع الإنتاج

1. عقد HMAC الداخلي لا يمنع replay/body/path tampering وفق العقد المطلوب.
2. AI Credits/dispatch لا تستخدم persistent outbox/reservation/materialization saga.
3. لا يوجد ضمان idempotency دائم على output/materialization أو transition state machine.
4. OpenAPI غير صالح كجودة إصدار (116 errors و12 warnings).
5. لا توجد أدلة PostgreSQL/Redis/Celery/S3/contract/E2E أو Docker startup/backup-restore.
6. `/api/` و`/api/v1/` يقدمان surface متكرراً بلا deprecation contract.

### P1 — يجب حلها قبل الإنتاج

1. bindings المصدر/manifest/download غير job-scoped ولا signed قصيرة العمر حسب العقد.
2. لا توجد queue separation وbeat/reconciliation policy المطلوبة.
3. لا توجد ملاحظة تشغيلية مثبتة لـJSON logging، metrics، Sentry، rate limiting وrunbooks.
4. حالة HSTS preload تحتاج قرار نطاق/DNS فعلي قبل تفعيلها.
5. توجد broad exceptions وdocumentation/OpenAPI gaps تحتاج تغطية اختبارية.

## 11. حدود هذا الخط الأساس وقرار المتابعة

لا يجري Phase 0 تغييرات على code paths. لا يجوز الانتقال إلى Phase 1 قبل اعتماد هذا السجل كأساس. الترتيب التالي المقترح، من دون تنفيذه هنا، هو:

1. توحيد surface وعقد AI وإبقاء `apps.ai_integration` وحده نشطاً مع خطة توافق للـpublic API.
2. بناء HMAC canonical والـnonce storage/اختباراته.
3. بناء AI job saga/outbox/credit reservation والمigrations الاختبارية.
4. تقوية materializers، RBAC/IDOR، envelope/OpenAPI، ثم تشغيل stack PostgreSQL/Redis/Celery/S3 وDocker/Coolify والـrunbooks.

**لا توجد أدلة كافية لوسم الإصدار الحالي READY FOR STAGING أو READY FOR CONTROLLED PRODUCTION CANARY. القرار الحالي الوحيد: `BLOCKED`.**
