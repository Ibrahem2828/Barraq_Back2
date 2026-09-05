# تدقيق جاهزية Baraq Backend للإنتاج وربطه بخدمة AI

تاريخ التدقيق: 2026-07-25  
النطاق: مستودع Django في `Baraaq_back/backend`، وقراءة تقرير خدمة AI المشار إليه من المستخدم.  
النطاق غير المشمول: تنفيذ خدمة AI المنفصلة أو إعداد Coolify الفعلي على الخادم؛ لا يمكن إثباتهما من هذا المستودع.

## القرار التنفيذي

**لا يُعتمد الإطلاق الإنتاجي الكامل الآن.** كود Django النشط اجتاز اختبارات التشغيل المحلية وبوابة الإصدار، لكن توجد نقطتا حجب خارج هذا المستودع:

1. عقد الربط في تقرير خدمة AI يصف نمطاً مختلفاً عن النمط المنفذ في Django. يجب توحيد العقد قبل النشر المشترك.
2. فحص HTTPS الخارجي، من بيئة مستقلة وبصلاحية شبكة موسعة، انتهى بمهلة زمنية لكل من مسارات صحة `api` و`ai`. هذا لا يثبت أن الخدمة متوقفة، لكنه يعني أنه لا توجد شهادة تشغيل خارجية ناجحة حالياً.

النتيجة العملية: النسخة مناسبة لـ **staging مضبوط** بعد تطبيق قائمة الربط أدناه، وليست جاهزة بعد لوصفها بأنها «خالية من الأخطاء» أو لإطلاق إنتاجي عام.

## ما تم التحقق منه فعلياً

| الفحص | النتيجة | الملاحظة |
|---|---:|---|
| `python -m compileall -q apps config scripts` | ناجح | لا توجد أخطاء تركيب Python. |
| `python manage.py check` | ناجح | باستخدام إعداد تطوير وقاعدة اختبار محلية معزولة. |
| `python manage.py makemigrations --check --dry-run` | ناجح | لا توجد migrations ناقصة. |
| `python manage.py test` | **125/125 ناجح** | للتطبيقات المسجلة والفعالة فقط. |
| `python scripts/validate_release.py` | ناجح | فحص 259 ملف Python، صفر أخطاء. |
| `python manage.py check --deploy` | ناجح مع تحذيرات | تحذيرات OpenAPI وHSTS preload، موضحة أدناه. |
| Health خارجي لـ API وAI | غير محسوم | أربع مهلات زمنية؛ يجب إعادة الفحص من خادم Coolify أو شبكة مراقبة. |
| Ruff | لم يُشغّل | حزمة Python موجودة لكن executable `ruff.exe` غير متاح في البيئة الحالية. |

اختبارات Django تستعمل `MD5PasswordHasher` فقط عند تنفيذ `manage.py test` لتقليل زمن CI؛ لا يؤثر ذلك على التجزئة في التطوير أو الإنتاج.

## التعديلات المنفذة في هذا التدقيق

1. **صدى (Sada) صار يقبل مصدراً صوتياً واحداً فقط.** حُظر تمرير مجلد لأنه قد يحوي عدة تسجيلات أو ملفات غير صوتية، ولا ينتج عنه تفريغ واحد غير ملتبس.
2. **إتاحة الشخصيات صارت مرتبطة بالاشتراك.** واجهة capabilities لا تعرض «خلاصة» أو «صدى» كمتاحين لمستخدم لا تتيح خطته الشخصية المعنية.
3. **تثبيت سياق ملكية المصدر في AI.** روابط manifest التي ينشئها Django تحمل `user_id`، ويتحقق Django من مطابقته عندما يرده AI. روابط download الناتجة تحمل السياق نفسه.
4. **حماية طلبات Django الصادرة إلى AI من redirect.** لا يتبع العميل التحويلات؛ فلا يمكن تسريب مفتاح الخدمة أو توقيع HMAC إلى origin آخر.
5. **تصحيح دلالة HTTP للتغذية الراجعة.** أول إنشاء يعيد `201`، والتحديث idempotent يعيد `200`.
6. **فصل اختبارات التطبيقات النشطة عن النماذج الأولية غير المسجلة.** أمر `manage.py test` يكتشف اختبارات التطبيقات الموجودة في `INSTALLED_APPS` فقط. يمكن تشغيل أي حزمة قديمة صراحةً باسمها، لكنها ليست جزءاً من المنتج المنشور.
7. **إصلاح بوابة الإصدار.** الملفات المحجوزة غير المتتبعة محلياً مثل `.env` وقواعد SQLite لا تفشل البوابة، لكنها تظل ممنوعة إذا كانت متتبعة في Git. أزيل `db.sqlite3-journal` المتولد والمتتبع بالخطأ.

## خريطة الكود النشط

| الوحدة | المسؤولية | ملاحظة إنتاجية |
|---|---|---|
| `config` | الإعدادات، ASGI/WSGI، Celery، المسارات | إعداد إنتاجي جيد: Hosts وTLS وHSTS وCORS وPostgreSQL وRedis. |
| `common` | envelope للأخطاء، middleware، health، logging | readiness يفحص DB وcache وstorage ويعيد 503 عند التعطل. |
| `users` | JWT، التسجيل، كلمات المرور، البريد | JWT من `simplejwt` داخل Django. |
| `students`, `subjects` | الملف الأكاديمي والمراحل والمواد | المصدر المرجعي لسياق الطالب. |
| `subscriptions` | الخطط، الميزات، الحدود، الحجز والرد | Django هو صاحب قرار الرصيد والاستخدام. |
| `sources` | رفع الملفات، فحص الامتداد والتوقيع، المجلدات | حد الرفع `STUDENT_SOURCE_MAX_UPLOAD_MB`، افتراضياً 25MB. |
| `study_plans`, `quizzes` | خطط الدراسة والاختبارات ومحاولاتها | يستقبلان materialization موثقاً من AI. |
| `analytics`, `summaries`, `audio` | التوصيات والملخصات والتفريغ | ناتج AI النهائي يصبح سجلات Django. |
| `notifications`, `support`, `admin_dashboard` | التشغيل والإدارة والدعم | صلاحيات الإدارة منفصلة عن المستخدم العادي. |
| `ai_integration` | **البوابة الفعالة لخدمة AI** | ينشئ jobs، يحجز الاستخدام، يرسل للـAI، يتحقق من webhook ويحوّل الناتج. |

### كود موجود لكنه غير نشط

`apps.ai_gateway` و`apps.ai_platform` **غير موجودين في `INSTALLED_APPS` ولا تُضمّن مساراتهما**. هما نماذج أولية قديمة، وفيهما TODOs وmock logic وكود لا يمر باختبارات التطبيق النشط. لا تُفعّلهما ولا تضع مفاتيح OpenAI فيهما. القرار الصحيح لاحقاً هو حذفهما من Git بعد أرشفتهما أو نقل ما يلزم منه إلى خدمة AI المستقلة؛ إبقاؤهما لا يعطل تطبيق Django الحالي لكنه يزيد عبء الصيانة والتدقيق.

## المعمارية الفعلية المنفذة في Django

```text
تطبيق الويب/الجوال
        │ Bearer JWT
        ▼
https://api.barraq.xn--mgbaab0cxheq.tech/api/v1/ai/jobs/
        │ حجز الاشتراك + سجل AIJob + Celery
        ▼
Django worker ── HMAC + Internal Key ──► خدمة AI المستقلة
        │                                      │
        │                         PostgreSQL/Redis/OpenAI/RAG في خدمة AI
        ▼                                      │
Django webhook ◄──── HMAC callback ───────────┘
        │
        ├─ Quiz / StudyPlan / Recommendation / Summary / Transcription
        └─ Notification للمستخدم
```

هذا هو النمط الذي ينفذه المستودع: **Django هو gateway وصاحب الهوية والاشتراك والـmaterialization**. لا يتصل العميل مباشرة بقاعدة AI أو Redis أو OpenAI.

## عقد الربط الفعلي مع خدمة AI

### Django إلى AI

الإعدادات الحالية:

```env
AI_SERVICE_BASE_URL=https://ai.barraq.xn--mgbaab0cxheq.tech
AI_SERVICE_JOBS_PATH=/api/ai/v1/jobs
AI_SERVICE_FEEDBACK_PATH=/api/ai/v1/feedback
AI_SERVICE_HEALTH_PATH=/api/ai/v1/health/ready
AI_SERVICE_VERIFY_SSL=True
```

الطلبات الصادرة هي:

```text
POST {AI_SERVICE_BASE_URL}/api/ai/v1/jobs                 إنشاء job
GET  {AI_SERVICE_BASE_URL}/api/ai/v1/jobs/{external_id}   تحديث حالة job
POST {AI_SERVICE_BASE_URL}/api/ai/v1/jobs/{external_id}/cancel
POST {AI_SERVICE_BASE_URL}/api/ai/v1/feedback
GET  {AI_SERVICE_BASE_URL}/api/ai/v1/health/ready
```

رؤوس كل طلب صادرة من Django:

```text
X-Baraq-Internal-Key: <AI_SERVICE_INTERNAL_API_KEY>
X-Baraq-Timestamp: <unix seconds>
X-Baraq-Signature: HMAC-SHA256(timestamp + "." + raw-json-body)
Idempotency-Key: <AIJob public UUID أو feedback key>
Content-Type: application/json
```

لا يتبع العميل redirects. يجب أن تعيد خدمة AI `2xx` وJSON فيه `data.job_id` أو `data.id` عند إنشاء job.

الـpayload الأدنى لإنشاء job:

```json
{
  "client_job_id": "uuid",
  "user_id": 123,
  "character": "fahes",
  "task_type": "fahes_generate_quiz",
  "source_id": 45,
  "collection_id": null,
  "subject_id": 3,
  "input": {},
  "parameters": {},
  "callback_url": "https://api.../api/internal/v1/ai/webhooks/jobs/",
  "source_manifest_url": "https://api.../sources/45/manifest/?user_id=123",
  "collection_manifest_url": null,
  "user_context_url": "https://api.../users/123/context/"
}
```

### AI إلى Django

| المسار | الغرض | الحماية الحالية |
|---|---|---|
| `POST /api/internal/v1/ai/webhooks/jobs/` | نتيجة أو تحديث AIJob | HMAC timestamp/body عبر `AI_SERVICE_WEBHOOK_SECRET`. |
| `GET /api/internal/v1/ai/sources/{id}/manifest/?user_id={id}` | وصف المصدر ورابط التنزيل | `X-Baraq-Internal-Key`، ويتحقق من المالك إذا أرسل `user_id`. |
| `GET /api/internal/v1/ai/sources/{id}/download/?user_id={id}` | الملف الثنائي | المفتاح نفسه وملكية `user_id`. |
| `GET /api/internal/v1/ai/collections/{id}/manifest/?user_id={id}` | مصادر المجلد | المفتاح نفسه وملكية `user_id`. |
| `GET /api/internal/v1/ai/users/{id}/context/` | الملف والسياق والأداء | المفتاح نفسه. |

على خدمة AI تمرير `X-Baraq-Internal-Key` عند فتح `download_url`، وعدم حذف `user_id` من الروابط التي يسلّمها Django.

حالات `AIJob` هي:

```text
created → queued → submitted → processing → validating → completed
                                             ↘ failed / canceled
```

عند الفشل أو الإلغاء يعيد Django الاستخدام المحجوز، وعند اكتمال materialization يثبّت الاستخدام. توجد unique constraints لمنع تكرار job وfeedback للمستخدم نفسه.

### صدى والملفات

- الامتدادات الصوتية المقبولة: `mp3`, `m4a`, `wav` مع فحص ترويسة الملف.
- الحد الافتراضي للمصدر هو **25MB**، ويجب أن يكون هذا الحد مطابقاً لحد خدمة AI وواجهة المزود.
- صدى يقبل **ملفاً صوتياً واحداً** فقط؛ لا يقبل collection.
- إن احتاج المنتج تفريغ ملفات أطول، فالحل ليس رفع الحد في Django فقط؛ بل تقسيم/streaming مضبوط في AI مع حدود RAM وtimeout واختبار استعادة.

## التعارض الحرج مع تقرير خدمة AI المرفق

التقرير السابق يقترح أن يتصل العميل عبر reverse proxy إلى `/api/ai/v1/*` في AI، مع JWT غير متماثل وJWKS ومسارات `credits/reserve|commit|refund`. هذا **ليس** العقد المنفذ هنا:

| موضوع | Django الحالي | ما يذكره التقرير السابق | القرار المطلوب |
|---|---|---|---|
| دخول العميل | العميل يدخل Django فقط | العميل قد يصل AI عبر proxy | اختر نمط Django gateway؛ هو الأنسب لهذا المستودع. |
| JWT/JWKS | SimpleJWT داخل Django؛ لا يوجد `/.well-known/jwks.json` | AI يطلب RS256/ES256 وJWKS | لا تطلب JWKS من AI ما دام Django هو العميل؛ أو نفّذ التصميم الثاني كاملاً في المستودعين. |
| الرصيد | Django يحجز/يرد داخلياً في `subscriptions` | AI يستدعي endpoints credits | لا تجعل AI يستدعي credits حالياً؛ أو أضف عقداً transactionally كاملاً في Django وخدمة AI معاً. |
| قراءة المصادر | Internal key، وHMAC للـwebhook | HMAC موحد لكل الطلبات وnonce/replay | ترقية أمنية مشتركة مطلوبة قبل الإنتاج. |
| public AI domain | يستخدمه Django كـupstream HTTPS | يستخدم كـAPI عام أيضاً | لا تجعله public client API في هذا التصميم. |

**لا تخلط النموذجين.** الخيار الموصى به هو الإبقاء على Django gateway، وإلزام خدمة AI بقبول عقد HMAC أعلاه. إذا أصر المنتج على proxy مباشر من العميل إلى AI، فذلك مشروع تغيير مستقل يتطلب JWT RS/ES وJWKS وauthorization داخل AI وملكية المصدر وواجهات credits atomically؛ لا يكفي تعديل Nginx.

## فجوات يجب إغلاقها قبل الإنتاج

### P0 — تمنع الإطلاق

1. **توحيد عقد خدمة AI.** يجب أن تختبر خدمة AI فعلياً إنشاء job، قراءة manifest/download، callback موقّع، cancellation وfeedback مع هذا الباكند في staging.
2. **حل مهلات health الخارجية.** افحص DNS وIPv6/TLS وCoolify proxy وfirewall من خادم مستقل. يجب أن ينجح:

   ```text
   GET https://api.barraq.xn--mgbaab0cxheq.tech/api/health/live/  => 200
   GET https://api.barraq.xn--mgbaab0cxheq.tech/api/health/ready/ => 200
   GET https://ai.barraq.xn--mgbaab0cxheq.tech/.../health/live    => 200
   GET https://ai.barraq.xn--mgbaab0cxheq.tech/.../health/ready   => 200 أو 503 صحيح
   ```

3. **ترقية مصادقة AI→Django كإصدار متزامن.** الوضع الحالي يحمي webhooks بتوقيع زمني، لكن قراءات manifest/download تعتمد مفتاح خدمة ثابتاً ولا تطبق nonce/replay-cache. وحّد جميع الطلبات الداخلية على توقيع يشمل method + path + timestamp + content hash + nonce، واحفظ nonce في Redis لفترة قصيرة. لا تفعّل هذا جزئياً لأنه سيكسر AI القديم.
4. **اختبار حقيقي على PostgreSQL وRedis وCelery.** نتائج الاختبار الحالية SQLite معزولة؛ migrations وworker وstorage وwebhook يجب أن تختبر على compose staging.

### P1 — مطلوب قبل فتح التوثيق أو توسع الفريق

- `drf-spectacular` يصدر تحذيرات لأن عدداً من `APIView` لا يملك serializer/response schema، وهناك collisions لأسماء enum وحقل serializer في support غير محدد النوع. لا يوقف التشغيل، لكنه يجعل OpenAPI غير مكتمل. أضف `@extend_schema` response schemas و`@extend_schema_field` ثم اجعل schema generation صفراً تحذيرات في CI.
- `SECURE_HSTS_PRELOAD=False` هو تحذير deploy. لا تضبطه `True` إلا بعد تأكيد أن كل النطاقات الفرعية تعمل HTTPS دائماً؛ بعدها أضفه للقائمة الرسمية.
- لا تعتمد التخزين المحلي `media_data` إذا ستشغل أكثر من web/worker أو خادماً آخر؛ فعّل S3-compatible storage واختبر restore.
- احذف/أرشف `ai_gateway` و`ai_platform` بعد مراجعة المحتوى. لا تسمح لهما بإعادة إدخال مفاتيح مزود AI في Django.
- ثبت نسخة Ruff في بيئة CI وفعّل `ruff check apps config scripts`.

## إعداد Coolify المقترح

### تطبيق Django

1. أنشئ **Docker Compose Application** من هذا المستودع واستخدم `docker-compose.yml`.
2. اربط domain بالخدمة `web` على المنفذ الداخلي `8000`:

   ```text
   https://api.barraq.xn--mgbaab0cxheq.tech:8000
   ```

   لا تضف public ports لـPostgreSQL أو Redis أو `worker` أو `migrate`.
3. ضع الأسرار كـRuntime Environment Variables في Coolify، ولا ترفع `.env`.
4. اترك `migrate` ينفذ أولاً؛ entrypoint يشغل `migrate --noinput` و`collectstatic` ثم `check --deploy`، ولا يبدأ `web` و`worker` قبل نجاحه.
5. اضبط healthcheck للحاوية على `/api/health/live/`. استخدم `/api/health/ready/` للمراقبة الخارجية فقط، لأنه يفحص DB وRedis وstorage.

متغيرات Django الأساسية:

```env
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=<64+ random characters>
ALLOWED_HOSTS=api.barraq.xn--mgbaab0cxheq.tech
PUBLIC_API_BASE_URL=https://api.barraq.xn--mgbaab0cxheq.tech
CORS_ALLOWED_ORIGINS=<origins الدقيقة للويب فقط>
CSRF_TRUSTED_ORIGINS=https://api.barraq.xn--mgbaab0cxheq.tech,<dashboard-origin>

POSTGRES_PASSWORD=<random URL-safe secret>
POSTGRES_DB=baraq_db
POSTGRES_USER=baraq_user

AI_SERVICE_ENABLED=True
AI_SERVICE_BASE_URL=https://ai.barraq.xn--mgbaab0cxheq.tech
AI_SERVICE_VERIFY_SSL=True
AI_SERVICE_INTERNAL_API_KEY=<random 32+ chars>
AI_SERVICE_WEBHOOK_SECRET=<different random 32+ chars>

USE_S3_STORAGE=True
AWS_ACCESS_KEY_ID=<secret>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_STORAGE_BUCKET_NAME=<bucket>
AWS_S3_ENDPOINT_URL=<endpoint>
```

### تطبيق AI المنفصل

- شغّله في Coolify كتطبيق مستقل على `ai.barraq.xn--mgbaab0cxheq.tech` مع API وworker وPostgreSQL/Redis الخاصة به.
- لا تعرض قواعد بياناته أو Redis للعامة.
- يجب أن يسمح firewall/proxy للـAI بالوصول HTTPS إلى `api.../api/internal/v1/ai/*`، ولـDjango بالوصول إلى `ai...`.
- لا تستخدم `AI_SERVICE_VERIFY_SSL=False` في الإنتاج.
- لا تجعل تطبيق الهاتف يقرأ `ai...` مباشرةً في نموذج Django gateway.

## خطة إطلاق عملية

1. نفّذ التعديلات الحالية في Git commit مستقل.
2. انشر Django وAI إلى staging مع أسرار مختلفة تماماً عن الإنتاج.
3. نفّذ migration Django من قاعدة فارغة، ثم seed/bootstrap الإداري المطلوب.
4. نفّذ smoke test: تسجيل، login، رفع TXT وPDF وملف صوتي أصغر من 25MB، إنشاء Fahes وKhota وRasheed، ثم Sada بخطة تسمح به.
5. نفّذ عقد AI end-to-end: `reserve → job → manifest/download → webhook → materialize → commit`، وحالة failure/cancel/refund، وإعادة نفس `Idempotency-Key`.
6. اختبر أن user A لا يستطيع قراءة مصدر user B عبر manifest أو materialization، وأن webhook بتوقيع أو timestamp خاطئ يرفض.
7. اختبر backup/restore لـPostgreSQL وmedia/S3 وrollback لصورة Docker ذات tag ثابت.
8. بعد نجاح المراقبة والـsmoke tests، ابدأ canary محدوداً ثم راقب 5xx، زمن job، استخدام Celery، تكلفة AI واستهلاك الرصيد.

## معايير القبول النهائي

- جميع أوامر التحقق في هذا التقرير ناجحة في CI.
- لا توجد تحذيرات OpenAPI عند توليد schema، أو يوجد قرار صريح بإخفاء docs حتى إصلاحها.
- health checks العامة تنجح من مراقب خارجي.
- عقد واحد مكتوب ومختبر بين Django وAI؛ لا JWT/JWKS أو credits endpoints وهمية.
- webhook والـinternal reads موقعة مع replay protection.
- Sada متوافق حدّاً وتدفقاً مع خدمة AI الفعلية.
- PostgreSQL/Redis/Celery/S3 وعمليات النسخ والاستعادة جُرّبت في staging.

بعد استيفاء هذه المعايير يصبح الإطلاق التدريجي على Coolify مبرراً مهنياً.
