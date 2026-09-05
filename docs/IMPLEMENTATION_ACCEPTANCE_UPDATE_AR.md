# تحديث تنفيذ معيار اعتماد Backend

تاريخ التحديث: 21 أغسطس 2026

## ما تم تنفيذه في هذا التحديث

- أضيفت مساحة عمل `Project` مملوكة للمستخدم، مع حذف ناعم، أرشفة، timeline قابل للتدقيق، وAPI عند `/api/v1/projects/`.
- رُبطت المصادر والمجلدات والاختبارات والخطط وAI jobs اختيارياً بالمشروع، مع التحقق من المالك ومن تطابق المشروع مع المصدر أو المجلد.
- ثُبّت عقد AI v2 في الـjob: `contract_version` و`project_id` و`trace_context`، مع حفظ `quality_metrics` و`security_flags` ونسخة schema للمخرج.
- صارت حالات AI تمنع الانتقال إلى `completed` من refresh أو webhook عادي. لا يكتب `completed` إلا `complete_job` بعد materialization داخل transaction ناجحة.
- أضيف DB constraint يمنع حفظ AI job مكتمل بلا `result_type` و`result_id`.
- أضيف `UsageLedgerEntry` غير قابل للتعديل لعمليات reserve/commit/refund، ومفتاح تكرار ذري لكل عملية. بقي `SubscriptionUsage` projection متوافقاً مع العقود الحالية ويمكن إعادة بنائه من الـledger.
- صارت إشعارات نجاح أو فشل AI تحمل idempotency key لمنع التكرار عند replay.
- `PAYMENTS_ENABLED=False` هو الوضع الافتراضي الصريح؛ لا توجد routes دفع فعالة قبل ربط provider محقق واختبارات webhook/reconciliation.

## أدلة محلية قابلة لإعادة التنفيذ

نفذت في بيئة اختبار معزولة (SQLite محلياً وcache محلياً):

```powershell
python manage.py test
python manage.py makemigrations --check --dry-run
python scripts/validate_release.py
python -m compileall -q apps config scripts
```

النتيجة: 143 اختباراً ناجحاً، لا migrations معلقة، وRelease validator بلا أخطاء على 229 ملف Python.

## قرار الاعتماد الحالي

هذا التحديث لا يثبت مستوى B3 ولا يصح وصفه بـ`Production Approved` بعد. يلزم قبل ذلك، من بيئة staging/production الفعلية:

1. تشغيل Master E2E المتكامل مع Baraq.AI وMobile وDashboard 50 مرة متتالية.
2. اختبار التزامن على PostgreSQL وRedis الحقيقيين، بما في ذلك 100 reserve متوازٍ وreplay للـcallback.
3. تشغيل مزود دفع حقيقي أو Sandbox موثق مع webhook signature وout-of-order events وreconciliation؛ أو إبقاء الدفع مغلقاً في الإطلاق المجاني.
4. إثبات backup/restore وrollback، وقياس load/SLO وqueue metrics/alerts في بنية قريبة من الإنتاج.
5. إكمال فحوص SCA/SAST/SBOM وفتح/إغلاق findings بحسب سياسة الإصدار.

هذه البنود تعتمد على بنية وحسابات وخدمات خارج هذا المستودع، ولذلك لا تُستبدل باختبارات محلية أو نجاح endpoint شكلي.
