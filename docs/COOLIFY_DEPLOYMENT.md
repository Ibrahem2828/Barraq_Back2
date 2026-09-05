# نشر Baraq Backend على Coolify

## 1. نوع المورد

أنشئ Docker Compose application من المستودع وحدد `docker-compose.yml`. الخدمة العامة هي `web` والمنفذ الداخلي هو `8000`.

## 2. الدومين

اربط الدومين:

```text
https://api.barraq.xn--mgbaab0cxheq.tech
```

ويجب أن تكون القيم التالية مطابقة:

```env
ALLOWED_HOSTS=api.barraq.xn--mgbaab0cxheq.tech
PUBLIC_API_BASE_URL=https://api.barraq.xn--mgbaab0cxheq.tech
SECURE_SSL_REDIRECT=True
```

## 3. المتغيرات الإلزامية

انسخ المفاتيح من `.env.example` إلى Environment Variables داخل Coolify. أهم القيم:

- `SECRET_KEY`
- `POSTGRES_PASSWORD`
- `ALLOWED_HOSTS`
- `PUBLIC_API_BASE_URL`
- `CORS_ALLOWED_ORIGINS`
- `CSRF_TRUSTED_ORIGINS`
- `AI_SERVICE_BASE_URL`
- `BARAQ_HMAC_CURRENT_KEY_ID`
- `BARAQ_HMAC_KEYS_JSON`

لا تستخدم القيم المثالّية حرفياً.

## 4. ربط خدمة AI

إذا كانت خدمة AI في مشروع Coolify نفسه وعلى network مشتركة، اجعل `AI_SERVICE_BASE_URL` اسم خدمتها الداخلي. إذا كانت على دومين مستقل، استخدم HTTPS واضبط `AI_SERVICE_VERIFY_SSL=True`.

يجب أن تتمكن خدمة AI من الوصول إلى:

```text
/api/internal/v1/ai/webhooks/jobs/
/api/internal/v1/ai/sources/{id}/manifest/
/api/internal/v1/ai/sources/{id}/download/
/api/internal/v1/ai/collections/{id}/manifest/
/api/internal/v1/ai/users/{id}/context/
```

## 5. التخزين

Local volume مناسب لبيئة واحدة فقط. للإنتاج المتعدد الحاويات فعّل S3-compatible storage:

```env
USE_S3_STORAGE=True
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_STORAGE_BUCKET_NAME=...
AWS_S3_ENDPOINT_URL=...
AWS_S3_REGION_NAME=...
```

## 6. الصحة

- Liveness: `/api/v1/health/live/`
- Readiness: `/api/v1/health/ready/`

لا تستخدم readiness كـliveness، لأن readiness تتحقق من DB وRedis والتخزين.

## 7. ترتيب الإطلاق

1. PostgreSQL وRedis.
2. خدمة `migrate` لمرة واحدة.
3. خدمة `web`.
4. خدمة `worker`.
5. bootstrap للخطط والصلاحيات والمدير الأول.
6. Smoke tests.

## 8. Smoke tests

```bash
curl -fsS https://api.barraq.xn--mgbaab0cxheq.tech/
curl -fsS https://api.barraq.xn--mgbaab0cxheq.tech/api/v1/health/live/
curl -fsS https://api.barraq.xn--mgbaab0cxheq.tech/api/v1/health/ready/
curl -fsS https://api.barraq.xn--mgbaab0cxheq.tech/api/v1/
```

تحقق بعد ذلك من التسجيل، الدخول، رفع مصدر صغير، إنشاء AI job، وصول webhook، وعرض النتيجة.

## 9. Rollback

- استخدم صورة Docker ذات tag ثابت لكل إصدار.
- خذ نسخة احتياطية من PostgreSQL قبل migrations.
- لا تنفذ destructive migrations في نفس إصدار تغيير الكود دون خطة مرحلية.
- أعد الإصدار السابق ثم نفذ data rollback مدروساً عند الحاجة؛ لا تنفذ migrate backwards آلياً في الإنتاج.
