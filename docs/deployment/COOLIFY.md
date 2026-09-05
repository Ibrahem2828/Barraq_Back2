# نشر Baraq Backend على Coolify

## إعداد التطبيق

- النوع: **Docker Compose Application**.
- Base directory: جذر المستودع.
- Compose file: `docker-compose.yml`.
- الخدمة العامة: `web` على المنفذ الداخلي `8000`.
- الدومين: `https://api.barraq.xn--mgbaab0cxheq.tech`.
- Dockerfile يبني صورة Python 3.12 متعددة المراحل، غير root، وتشغّل Gunicorn عبر `tini`.

لا تنشر `db` أو `redis` أو `worker` أو `migrate` للعامة. اربط domain بخدمة `web` فقط.

## Runtime Variables

أضف المتغيرات داخل Coolify كـruntime values، ولا ترفع `.env` ولا تضع أسراراً في build arguments. المرجع الكامل هو [ENVIRONMENT.md](ENVIRONMENT.md).

القيم الحرجة:

```env
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=<64+-random-characters>
ALLOWED_HOSTS=api.barraq.xn--mgbaab0cxheq.tech
PUBLIC_API_BASE_URL=https://api.barraq.xn--mgbaab0cxheq.tech
CORS_ALLOWED_ORIGINS=https://dashboard.xn--mgbaab0cxheq.tech
CSRF_TRUSTED_ORIGINS=https://api.barraq.xn--mgbaab0cxheq.tech,https://dashboard.xn--mgbaab0cxheq.tech
POSTGRES_PASSWORD=<random-url-safe-secret>
BARAQ_HMAC_CURRENT_KEY_ID=django-current
BARAQ_HMAC_KEYS_JSON={"django-current":"<32+-random-secret>"}
```

اضبط `AI_SERVICE_BASE_URL` على عنوان HTTPS لخدمة AI. إن كانت الخدمة داخل شبكة خاصة فقط وتحتاج HTTP، اضبط **كلا** `AI_SERVICE_ALLOW_INSECURE_HTTP=True` و`AI_SERVICE_VERIFY_SSL=False` بعد تأكيد أنها غير مكشوفة للعامة.

## تسلسل الإقلاع

1. ينتظر `entrypoint.sh` PostgreSQL، بحد أقصى 30 محاولة.
2. تشغّل خدمة `migrate` الترحيلات و`collectstatic` ثم `check --deploy`.
3. لا تبدأ `web` و`worker` قبل نجاح `migrate`.
4. يبدأ Gunicorn على `0.0.0.0:8000`.

لا تشغّل `makemigrations` داخل startup أو deployment.

## Healthchecks

- Liveness للحاوية: `/api/v1/health/live/` — لا يعتمد على DB أو Redis أو التخزين.
- Readiness للمراقبة: `/api/v1/health/ready/` — يفحص database وcache وstorage ويعيد `503` عند التعطل.

الـDockerfile وCompose يستخدمان liveness الرسمي. لا تستبدل readiness بـliveness كي لا تعيد المنصة تشغيل عملية سليمة بسبب تعطل خدمة خارجية مؤقت.

## التحقق بعد النشر

نفّذ من شبكة مستقلة عن Coolify:

```bash
python scripts/production_smoke_test.py \
  --base-url https://api.barraq.xn--mgbaab0cxheq.tech
```

ثم نفذ عقد staging الفعلي: تسجيل/Login، رفع ملف صغير، Job AI، callback موقّع، materialization، reserve/commit/refund، وقياس worker/Redis.

## التخزين والرجوع

- Volume محلي مناسب لحاوية واحدة فقط. عند تعدد web/worker أو العقد فعّل S3-compatible storage واختبر restore.
- خذ نسخة PostgreSQL واختبر استعادتها قبل migration مهم.
- استخدم image tag ثابتاً لكل إصدار. لا تشغّل `migrate` إلى الخلف تلقائياً عند rollback؛ نفّذ خطة بيانات مدروسة.
