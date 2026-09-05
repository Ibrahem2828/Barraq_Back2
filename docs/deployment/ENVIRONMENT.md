# متغيرات البيئة للإنتاج

هذا المستند يصف فقط أسماء المتغيرات وقيمًا مثالّية آمنة. تُحفظ القيم الحقيقية كـRuntime Secrets داخل Coolify ولا توضع في Git أو Docker image.

## أساسية

| المتغير | إلزامي في الإنتاج | مثال آمن | ملاحظة |
|---|---:|---|---|
| `ENVIRONMENT` | نعم | `production` | وصف البيئة التشغيلي. |
| `DEBUG` | نعم | `False` | يجب أن يبقى معطلاً في الإنتاج. |
| `SECRET_KEY` | نعم، سري | `<64+-random-characters>` | يرفض التطبيق القيم القصيرة أو الضعيفة أو ذات بادئة Django التطويرية. |
| `ALLOWED_HOSTS` | نعم | `api.barraq.xn--mgbaab0cxheq.tech` | قائمة CSV؛ يمنع `*` ويجب أن تضم hostname العنوان العام. |
| `PUBLIC_API_BASE_URL` | نعم | `https://api.barraq.xn--mgbaab0cxheq.tech` | HTTPS origin فقط، بلا path أو query. |
| `TIME_ZONE` | لا | `Asia/Damascus` | المنطقة الزمنية للتطبيق. |

## Browser security

| المتغير | إلزامي | مثال آمن | ملاحظة |
|---|---:|---|---|
| `CORS_ALLOWED_ORIGINS` | عند وجود Dashboard/Web | `https://dashboard.xn--mgbaab0cxheq.tech` | origins HTTPS فقط في الإنتاج، مفصولة بفواصل. لا تضف API origin دون مستهلك Web حقيقي. |
| `CSRF_TRUSTED_ORIGINS` | عند استخدام cookies/admin عبر origin | `https://api.barraq.xn--mgbaab0cxheq.tech,https://dashboard.xn--mgbaab0cxheq.tech` | يجب أن تتضمن scheme. الاسم القديم `DJANGO_CSRF_TRUSTED_ORIGINS` مرفوض صراحةً. |
| `CORS_ALLOW_CREDENTIALS` | لا | `False` | فعّله فقط عند الحاجة الفعلية إلى cookies عبر CORS. |
| `SECURE_SSL_REDIRECT` | نعم | `True` | يعتمد على `X-Forwarded-Proto` من Coolify. |
| `SECURE_HSTS_SECONDS` | نعم | `31536000` | لا تفعّل preload قبل تأكيد HTTPS لكل subdomain. |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | لا | `True` | راجع النطاقات الفرعية أولاً. |
| `SECURE_HSTS_PRELOAD` | لا | `False` | قرار DNS/TLS خارجي؛ لا يُفعّل تلقائياً. |

## PostgreSQL وRedis

| المتغير | إلزامي | مثال آمن | ملاحظة |
|---|---:|---|---|
| `DATABASE_URL` | نعم | `postgresql://user:<password>@db:5432/baraq_db` | سرّي؛ PostgreSQL فقط في الإنتاج. تأكد من hostname قابل للحل من الحاوية. |
| `DATABASE_CONN_MAX_AGE` | لا | `60` | زمن إبقاء الاتصال بالثواني. |
| `DATABASE_CONN_HEALTH_CHECKS` | لا | `True` | فحص صحة الاتصال المعاد استخدامه. |
| `DATABASE_CONNECT_TIMEOUT` | لا | `10` | مهلة فتح الاتصال بالثواني. |
| `DATABASE_STATEMENT_TIMEOUT_MS` | لا | `30000` | حد PostgreSQL للاستعلام. |
| `REDIS_URL` | نعم عند استخدام cache/Celery | `redis://redis:6379/0` | سرّي إن احتوى كلمة مرور. |
| `CELERY_BROKER_URL` | لا | `redis://redis:6379/0` | افتراضه `REDIS_URL`. |
| `CELERY_RESULT_BACKEND` | لا | `redis://redis:6379/1` | افصله عن broker. |
| `CELERY_WORKER_CONCURRENCY` | لا | `2` | يخص خدمة worker في Compose. |

`POSTGRES_DB` و`POSTGRES_USER` و`POSTGRES_PASSWORD` تخص خدمة PostgreSQL المعرفة في `docker-compose.yml`. كلمة المرور سرية، ويُبنى `DATABASE_URL` داخل الشبكة من هذه القيم.

## خدمة الذكاء الاصطناعي الداخلية

| المتغير | إلزامي عندما `AI_SERVICE_ENABLED=True` | مثال آمن | ملاحظة |
|---|---:|---|---|
| `AI_SERVICE_ENABLED` | نعم | `True` | عند `False` لا ينفذ التطبيق طلبات AI. |
| `AI_SERVICE_BASE_URL` | نعم | `https://ai.example.invalid` | HTTPS مطلوب في الإنتاج. HTTP الداخلي يتطلب الاستثناء الصريح أدناه. |
| `AI_SERVICE_JOBS_PATH` | لا | `/api/ai/v1/jobs` | مسار إنشاء/قراءة job في خدمة AI. |
| `AI_SERVICE_FEEDBACK_PATH` | لا | `/api/ai/v1/feedback` | مسار feedback. |
| `AI_SERVICE_HEALTH_PATH` | لا | `/api/ai/v1/health/ready` | مسار health الداخلي. |
| `AI_SERVICE_VERIFY_SSL` | لا | `True` | يجب أن يبقى مفعلاً مع HTTPS. |
| `AI_SERVICE_ALLOW_INSECURE_HTTP` | لا | `False` | لا يفعّل إلا لاتصال داخلي موثوق داخل شبكة خاصة؛ عندها اجعل `AI_SERVICE_VERIFY_SSL=False`. |
| `AI_SERVICE_TIMEOUT_SECONDS` | لا | `30` | كل طلب AI يملك timeout. |
| `AI_DATASET_CONSENT_VERSION` | لا | `2026-07-01` | نسخة موافقة البيانات للتغذية الراجعة. |
| `BARAQ_SERVICE_ID` | لا | `baraq-django` | هوية خدمة Django في HMAC V2. |
| `BARAQ_HMAC_CURRENT_KEY_ID` | نعم عند تفعيل AI، سري بالمعرّف | `django-current` | معرف المفتاح المستخدم في التوقيع. |
| `BARAQ_HMAC_KEYS_JSON` | نعم عند تفعيل AI، سري | `{"django-current":"<32+-random-secret>"}` | keyring JSON لمفتاح حالي وربما سابق أثناء rotation. |
| `BARAQ_HMAC_ALLOWED_SERVICES` | لا | `baraq-ai-service` | قائمة الخدمات المخولة بإرسال طلبات داخلية. |
| `BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS` | لا | `300` | الحد المسموح لفارق الساعة. |
| `BARAQ_HMAC_NONCE_TTL_SECONDS` | لا | `600` | TTL لحماية replay في Redis. |

لا تستخدم المتغيرات القديمة `AI_SERVICE_INTERNAL_API_KEY` أو `AI_SERVICE_WEBHOOK_SECRET`: التطبيق لا يقرأها. بروتوكول الربط الوحيد هو Baraq HMAC V2.

## التخزين والبريد والمراقبة

| المجموعة | المتغيرات |
|---|---|
| رفع الملفات | `STUDENT_SOURCE_MAX_UPLOAD_MB`, `MEDIA_ROOT`, `MEDIA_URL` |
| S3 اختياري | `USE_S3_STORAGE`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ENDPOINT_URL`, `AWS_S3_REGION_NAME` — مفاتيح AWS سرية. |
| البريد | `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, `FRONTEND_PASSWORD_RESET_URL`, `PASSWORD_RESET_TIMEOUT` — كلمة المرور سرية. |
| JWT/throttling | `ACCESS_TOKEN_LIFETIME_MINUTES`, `REFRESH_TOKEN_LIFETIME_DAYS`, `THROTTLE_ANON`, `THROTTLE_USER`, `THROTTLE_REGISTER`, `THROTTLE_LOGIN`, `THROTTLE_PASSWORD_RESET`, `THROTTLE_UPLOADS`, `THROTTLE_AI` |
| التشغيل | `PORT`, `GUNICORN_WORKERS`, `GUNICORN_THREADS`, `GUNICORN_TIMEOUT`, `GUNICORN_GRACEFUL_TIMEOUT`, `GUNICORN_KEEPALIVE`, `GUNICORN_MAX_REQUESTS`, `GUNICORN_MAX_REQUESTS_JITTER` |
| entrypoint | `DJANGO_WAIT_FOR_DATABASE`, `DJANGO_RUN_MIGRATIONS`, `DJANGO_COLLECTSTATIC` |
| المراقبة | `LOG_LEVEL`, `APP_LOG_LEVEL`, `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE` — DSN يعامل كقيمة حساسة. |

## التحقق

بعد ضبط Runtime Variables فقط شغّل:

```bash
python scripts/production_preflight.py
python scripts/production_smoke_test.py --base-url https://api.barraq.xn--mgbaab0cxheq.tech
```

لا تنسخ `.env.example` حرفياً إلى الإنتاج؛ هي قائمة أسماء وقيم بديلة تعليمية فقط.
