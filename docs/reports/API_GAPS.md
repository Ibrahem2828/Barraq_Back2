# API Gaps Report

المصدر التقني للعقد هو `docs/api/openapi.json` المُصدّر من URLConf وdrf-spectacular. لا تُنشأ business APIs لتغطية افتراضات غير مثبتة في الكود.

## Intentional / documented

| الحالة | الموضوع | القرار |
|---|---|---|
| Partial | Payments | `PAYMENTS_ENABLED=False` افتراضياً ولا يوجد provider/webhook/reconciliation موثق. تبقى المدفوعات مغلقة حتى يضاف adapter واختبارات sandbox/replay. |
| Deprecated | `/api/` legacy surface | يبقى للتوافق مع clients القديمة حتى `31 Dec 2026` مع headers deprecation؛ العقود الجديدة تستعمل `/api/v1/` فقط. |
| Needs clarification | Dashboard web origin | العقد يستعمل `https://dashboard.xn--mgbaab0cxheq.tech` كما في إعدادات الإنتاج الحالية؛ تأكيد DNS/TLS والمالك الخارجي مطلوب قبل الإطلاق. |

## External blockers, not missing Django endpoints

| الحالة | الموضوع | الدليل/الإجراء المطلوب |
|---|---|---|
| Broken | قاعدة البيانات المعينة محلياً | hostname في `DATABASE_URL` غير قابل للحل من بيئة التشغيل. أعد إدخال connection URL الكامل من مزود PostgreSQL أو استخدم خدمة `db` داخل Compose. |
| Partial | عقد خدمة AI | Django يطبق HMAC V2 وnonce replay protection. يلزم اختبار staging متبادل مع خدمة AI لإنشاء job، manifest/download، webhook، cancel وfeedback. |
| Partial | S3/backup/restore | الكود يدعم S3، لكن لا يوجد دليل تشغيل خارجي على restore أو multi-replica media. |

## العقود المتاحة

- Mobile: `docs/api/mobile-api.json`.
- Dashboard: `docs/api/dashboard-api.json` (كلها تحت `/api/v1/admin/` ومحكومة RBAC).
- Internal AI: موجود في `docs/api/openapi.json` فقط، وليس عقداً لتطبيق Mobile أو Dashboard.

شغّل `python scripts/export_api_contracts.py --check` في CI لمنع documentation drift.
