# Production Readiness Status

## ما تم إغلاقه في هذه النسخة

- حذف طبقات AI المدمجة القديمة وإبقاء integration layer فقط.
- إزالة الأسرار وقواعد البيانات والملفات المؤقتة من الحزمة.
- إصلاح تعريفات مكررة وأخطاء URL router.
- API v1 موحد مع legacy compatibility.
- صفحات root و404 و500 بشرية، وأخطاء API بصيغة JSON.
- JWT rotation/blacklist وthrottling.
- فصل liveness عن readiness.
- PostgreSQL + Redis + Celery + non-root Docker image.
- Upload validation وOffice archive safety checks.
- AI Jobs idempotency، credits reserve/refund، webhook HMAC، وحماية terminal states.
- Materialization لجميع شخصيات برّاق.
- RBAC للداشبورد، دعم، إشعارات، وإدارة المحتوى.
- اختبارات محدثة للتدفقات الرئيسية.

## بوابة CI الآلية (`.github/workflows/ci.yml`)

كل push/PR يشغّل تلقائيًا، ضد PostgreSQL حقيقي (service container)، بدون `continue-on-error`:

```bash
ruff check apps config scripts
python manage.py makemigrations --check --dry-run
python manage.py test --verbosity 2
python manage.py spectacular --file /tmp/schema.yaml --fail-on-warn
python scripts/validate_release.py
```

توليد OpenAPI schema يمر حاليًا **بصفر تحذيرات** (تم إغلاق جميع فجوات `@extend_schema` وتعارضات تسمية enum عبر `ENUM_NAME_OVERRIDES`).

## بوابة الإطلاق الإلزامية (يدوية، تتطلب Staging حقيقي)

لا يعتمد الإصدار إنتاجياً قبل نجاح التالي داخل Staging مطابق للإنتاج (هذه الخطوات لا يمكن لأي CI آلي إثباتها لأنها تتطلب DNS/TLS/بنية تحتية فعلية):

- Docker build دون warnings مانعة.
- database backup/restore drill.
- upload tests للأنواع المدعومة والملفات الخبيثة.
- concurrent credits/idempotency tests.
- AI webhook replay/signature tests.
- end-to-end من الجوال إلى AI ثم materialization.
- dashboard RBAC matrix.
- **load test**: `k6 run -e BASE_URL=https://staging... -e EMAIL=... -e PASSWORD=... scripts/load_test_k6.js` — يغطي `POST /api/v1/auth/login/` و`POST /api/v1/ai/jobs/` (حجز رصيد AI عبر `select_for_update`) تحت تزامن حقيقي. عتبات القبول: `p(95) < 800ms`, `error rate < 1%`.
- HTTPS وCORS وsecurity headers audit.

## المراقبة الإنتاجية (Monitoring)

- **الأخطاء**: `SENTRY_DSN` مُفعَّل فعليًا في `config/settings.py` (traces_sample_rate قابل للضبط عبر `SENTRY_TRACES_SAMPLE_RATE`). اربط مشروع Sentry فعليًا قبل الإطلاق ولا تتركه فارغًا.
- **الاستعلامات البطيئة في PostgreSQL**: فعّل `pg_stat_statements` extension على قاعدة الإنتاج (`CREATE EXTENSION IF NOT EXISTS pg_stat_statements;`) وراقبها دوريًا؛ `DATABASE_STATEMENT_TIMEOUT_MS` (افتراضي 30 ثانية) موجود بالفعل كحاجز أمان لمنع استعلام معلّق من إغراق worker.
- **توفر الخدمة**: `/api/health/live/` (فحص حاوية بسيط) و`/api/health/ready/` (يفحص DB/cache/storage فعليًا، استخدمه للمراقبة الخارجية فقط لأنه أثقل).
- **Celery**: راقب طول الطابور وعدد retries عبر Redis (`CELERY_BROKER_URL`)؛ لا توجد لوحة مدمجة حاليًا — استخدم Flower أو تصدير Celery events لأي أداة مراقبة قائمة (Grafana/Prometheus) إن توفرت في البنية التحتية.

## ما لا يمكن ضمانه بالفحص الساكن

الفحص الساكن يثبت سلامة syntax والبنية وعدم وجود تعريفات مكررة أو ملفات محظورة وفق القواعد، لكنه لا يثبت توافق dependencies أو migrations أو الشبكة أو مزود التخزين أو AI service. هذه الأمور تتطلب بناء الحاويات وتشغيل Staging.
