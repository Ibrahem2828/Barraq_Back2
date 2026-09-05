# عقد الربط بين Django وخدمة الذكاء الاصطناعي

## المسؤوليات

### Django Backend

- المصادقة والمستخدمون والصلاحيات.
- ملكية المصادر والمجلدات.
- الاشتراكات وحجز/رد الاستخدام.
- إنشاء AI Job محلي وإظهار حالته للجوال والداشبورد.
- استقبال webhook موقع.
- التحقق النهائي وتحويل المخرج إلى Quiz أو StudyPlan أو Recommendation أو Summary أو Transcription.

### AI Service

- مزودو LLM وModel Router.
- Prompt Registry وStructured Output.
- RAG وEmbeddings وRetrieval.
- معالجة PDF/DOCX/PPTX والصوت.
- Token/cost/latency telemetry.
- تقييم الجودة وتجهيز Dataset.

## الاتصال الصادر

يرسل Django إلى:

```text
POST {AI_SERVICE_BASE_URL}{AI_SERVICE_JOBS_PATH}
GET  {AI_SERVICE_BASE_URL}{AI_SERVICE_JOBS_PATH}/{job_id}
POST {AI_SERVICE_BASE_URL}{AI_SERVICE_JOBS_PATH}/{job_id}/cancel
POST {AI_SERVICE_BASE_URL}{AI_SERVICE_FEEDBACK_PATH}
```

الرؤوس:

```text
X-Baraq-Internal-Key
X-Baraq-Timestamp
X-Baraq-Signature
Idempotency-Key
```

التوقيع HMAC-SHA256 لـ`timestamp.body`.

## Webhook

```text
POST /api/internal/v1/ai/webhooks/jobs/
```

الحد الأدنى للطلب:

```json
{
  "event_id": "unique-event-id",
  "event_type": "job.completed",
  "job_id": "external-job-id",
  "status": "completed",
  "result": {},
  "metadata": {}
}
```

Webhook idempotent بواسطة `event_id`. النتيجة المتأخرة لا تعيد فتح job ملغى أو فاشل.

## حالات AI Job

```text
created -> queued -> submitted -> processing -> validating -> completed
                                                -> failed
                                                -> canceled
```

## Materialization

- فاحص: Quiz + Questions + Choices + private Question Bank.
- خطى: StudyPlan + StudyTasks.
- رشيد: StudentRecommendation.
- خلاصة: Summary.
- صدى: Transcription.

كل materialization داخل transaction. لا تعتمد نتيجة غير صالحة أو غير متوافقة مع قواعد المجال.

## Credits

1. Reserve عند إنشاء job.
2. Commit عند اكتمال materialization.
3. Refund عند الفشل أو الإلغاء.
4. idempotency يمنع الخصم المكرر لنفس الطلب.
