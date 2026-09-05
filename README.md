# Baraq Backend - Production Candidate 4.0

خدمة Django/DRF المركزية لمنصة برّاق. تدير الحسابات والطلاب والمواد والخطط والاختبارات والمصادر والاشتراكات والإشعارات والدعم ولوحة التحكم، وتتصل بخدمة الذكاء الاصطناعي المستقلة عبر Internal API موقعة.

## العناوين الأساسية

- Public root: `/`
- Canonical API: `/api/v1/`
- Health: `/api/health/live/`
- Readiness: `/api/health/ready/`
- OpenAPI schema: `/api/schema/`
- Swagger: `/api/docs/`
- Django admin: `/admin/`
- Internal AI callbacks/manifests: `/api/internal/v1/ai/`

العنوان الإنتاجي المقترح:

```text
https://api.barraq.xn--mgbaab0cxheq.tech
```

## المكونات

- Django 6 + Django REST Framework
- PostgreSQL
- Redis cache and Celery broker
- Celery workers للعمليات غير المتزامنة
- SimpleJWT مع refresh rotation وblacklist
- drf-spectacular لعقد OpenAPI
- WhiteNoise للـstatic assets
- S3-compatible storage اختياري للمصادر
- Sentry اختياري للمراقبة
- خدمة AI مستقلة؛ لا توجد مفاتيح OpenAI داخل هذا المستودع

## تشغيل محلي عبر Docker

```bash
cp .env.example .env
# عدّل جميع القيم التي تبدأ بـ replace-with

docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

ثم:

```bash
curl http://localhost:8000/api/health/live/
curl http://localhost:8000/api/health/ready/
```

## التهيئة الأولى

بعد نجاح migrations، خزّن بيانات المدير الأول كمتغيرات بيئة مؤقتة أو داخل Coolify:

```env
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=replace-with-a-strong-password
DJANGO_SUPERUSER_FULL_NAME=Baraq Super Admin
```

ثم نفذ:

```bash
docker compose exec web python manage.py bootstrap_baraq --create-superuser-from-env
```

الأمر idempotent ويجهز RBAC والخطط الافتراضية أيضاً. بعد النجاح احذف متغير كلمة مرور المدير من إعدادات التشغيل إن لم تعد تحتاجه.

## الاختبارات وبوابة الإصدار

الفحص الساكن الذي لا يحتاج dependencies:

```bash
python scripts/validate_release.py
python -m compileall . -q -f
```

الفحص الكامل داخل حاوية مبنية:

```bash
docker compose run --rm web python manage.py check --deploy
docker compose run --rm web python manage.py makemigrations --check --dry-run
docker compose run --rm web python manage.py test
docker compose run --rm web python manage.py spectacular --file /tmp/openapi.yaml --validate
```

## نشر Coolify

استخدم `docker-compose.yml`، واختر خدمة `web` كخدمة HTTP على المنفذ `8000`. عرّف متغيرات `.env.example` داخل Coolify ولا ترفع ملف `.env` أو أي مفتاح حقيقي إلى Git.

التفاصيل الكاملة في:

- `docs/COOLIFY_DEPLOYMENT.md`
- `docs/AI_SERVICE_INTEGRATION.md`
- `docs/API_CONTRACT.md`
- `docs/PRODUCTION_READINESS.md`

## ملاحظات أمنية

- لا تضع مفاتيح OpenAI أو مفاتيح مزودي LLM في Django أو تطبيق الجوال.
- مفاتيح المزودين تخص خدمة AI المستقلة فقط.
- استخدم HTTPS في الإنتاج.
- استخدم S3-compatible object storage عند تشغيل أكثر من حاوية أو أكثر من worker.
- Swagger خاص بالمدير افتراضياً (`API_DOCS_PUBLIC=False`).
- غيّر كل الأسرار وكلمات المرور قبل أول نشر.
