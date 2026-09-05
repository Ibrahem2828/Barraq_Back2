# Student Sources

## المفهوم

`StudentSourceCollection` هو مجلد أو مشروع دراسي يملكه الطالب، مثل: الرياضيات، محاضرات الذكاء الاصطناعي، كتاب التحليل، أو مشروع التخرج.

`StudentSource` هو ملف داخل النظام، ويمكن أن يكون داخل collection أو مستقلًا كما كان سابقًا. مسارات `student-sources` القديمة لم تتغير.

## رفع مصدر

`POST /api/student-sources/`

`multipart/form-data`:

- `title`: مطلوب.
- `description`: اختياري.
- `subject`: اختياري.
- `collection`: اختياري.
- `file`: مطلوب.

الباك يستنتج `user`, `source_type`, `file_size`, `mime_type`, `extension`, و`status`.

إذا كان `collection` يملك `subject` ولم يرسل المصدر مادة، يرث المصدر مادة المجلد تلقائيًا.

## قيود الملفات

الحد الأقصى الافتراضي 25MB عبر `STUDENT_SOURCE_MAX_UPLOAD_MB`.

الأنواع المدعومة الآن: PDF, TXT, JPG, JPEG, PNG, WEBP, DOC, DOCX, PPT, PPTX, MP3, M4A, WAV.

الملفات الخطيرة مثل `exe`, `sh`, `bat`, `cmd`, `js`, `html`, `php`, `py`, `jar`, `zip`, `rar`, `7z`, `sql`, و`env` مرفوضة برسالة 400.

أسماء الملفات العربية محفوظة في `original_filename`، بينما اسم التخزين الفعلي UUID آمن.

## معالجة المصدر

`POST /api/student-sources/{id}/process/`

ملفات TXT فقط تقرأ مباشرة. PDF/Word/PowerPoint/Image/Audio تحفظ دون معالجة ثقيلة وترجع:

`تم حفظ المصدر، والمعالجة المتقدمة لهذا النوع ستتوفر لاحقًا.`

لا يوجد OCR أو transcription أو AI حقيقي في هذه المرحلة.

## Endpoints المصادر

- `GET /api/student-sources/`
- `POST /api/student-sources/`
- `GET /api/student-sources/{id}/`
- `PATCH /api/student-sources/{id}/`
- `DELETE /api/student-sources/{id}/`
- `POST /api/student-sources/{id}/process/`
- `GET /api/student-sources/{id}/capabilities/`
- `POST /api/student-sources/{id}/use-with-character/`
- `POST /api/student-sources/{id}/use-with-khota/`
- `POST /api/student-sources/{id}/use-with-fahes/`
- `POST /api/student-sources/{id}/use-with-rasheed/`
- `POST /api/student-sources/{id}/use-with-kholasa/`
- `POST /api/student-sources/{id}/use-with-sada/`

## Endpoints المجلدات

- `GET /api/student-source-collections/`
- `POST /api/student-source-collections/`
- `GET /api/student-source-collections/{id}/`
- `PATCH /api/student-source-collections/{id}/`
- `DELETE /api/student-source-collections/{id}/`
- `GET /api/student-source-collections/{id}/sources/`
- `GET /api/student-source-collections/{id}/capabilities/`
- `POST /api/student-source-collections/{id}/use-with-character/`
- `POST /api/student-source-collections/{id}/use-with-khota/`
- `POST /api/student-source-collections/{id}/use-with-fahes/`
- `POST /api/student-source-collections/{id}/use-with-rasheed/`
- `POST /api/student-source-collections/{id}/use-with-kholasa/`
- `POST /api/student-source-collections/{id}/use-with-sada/`

الحذف الحالي يمنع حذف مجلد يحتوي مصادر:

`لا يمكن حذف مجلد يحتوي على مصادر. انقل المصادر أو احذفها أولًا.`

## الشخصيات

خُطى مع مجلد: تنشئ خطة مذاكرة عامة من مصادر المجلد إذا وُجدت مادة على المجلد أو أحد مصادره. إذا لم توجد مادة، ترجع رسالة 200 آمنة داخل interaction ولا ترجع 500.

فاحص مع مجلد: ينشئ Quiz draft، ويضيف أسئلة short answer بسيطة إذا وُجد `extracted_text` في مصادر TXT.

رشيد مع مجلد: يرجع نصائح تنظيمية حسب عدد المصادر وأنواعها وحالة النص المستخرج.

خلاصة: غير متوفرة حاليًا وتعيد رسالة coming soon.

صدى: غير متوفر حاليًا وتعيد رسالة coming soon.

## أخطاء رفع شائعة

- بدون ملف: `يرجى اختيار ملف لرفعه.`
- بدون عنوان: `يرجى إدخال عنوان للمصدر.`
- بدون امتداد: `تعذر تحديد نوع الملف. تأكد أن الملف يحتوي على امتداد صحيح.`
- امتداد غير مدعوم: `نوع الملف غير مدعوم: .ext`
- ملف خطير: `هذا النوع من الملفات غير مسموح به: .ext`
- ملف أكبر من الحد: `حجم الملف أكبر من الحد المسموح (25MB).`

كل هذه الحالات ترجع 400 وليس 500.
