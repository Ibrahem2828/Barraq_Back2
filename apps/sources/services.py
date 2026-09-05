from __future__ import annotations

import hashlib
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.ai_integration.models import AIJob
from apps.ai_integration.services import create_ai_job

from .models import StudentSource, StudentSourceInteraction

TXT_READ_LIMIT_BYTES = 2 * 1024 * 1024
logger = logging.getLogger(__name__)


def _sha256_file(source):
    digest = hashlib.sha256()
    with source.file.open('rb') as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text_file(source):
    with source.file.open('rb') as file_obj:
        content = file_obj.read(TXT_READ_LIMIT_BYTES + 1)
    if len(content) > TXT_READ_LIMIT_BYTES:
        raise ValidationError('الملف النصي كبير جدًا للمعالجة الفورية.')
    for encoding in ('utf-8-sig', 'utf-8', 'cp1256', 'latin-1'):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError('تعذر قراءة ترميز الملف النصي.')


@transaction.atomic
def process_source(source):
    source = StudentSource.objects.select_for_update().get(pk=source.pk)
    source.status = StudentSource.Status.PROCESSING
    source.processing_error = ''
    source.save(update_fields=['status', 'processing_error', 'updated_at'])
    try:
        metadata = {**(source.metadata or {}), 'sha256': _sha256_file(source)}
        if source.source_type == StudentSource.SourceType.TEXT:
            source.extracted_text = _read_text_file(source)
            source.status = StudentSource.Status.READY
            metadata['processing'] = 'text_extracted'
            message = 'تمت قراءة الملف النصي وحفظ محتواه.'
        else:
            # Advanced extraction is delegated to the standalone AI service.
            source.status = StudentSource.Status.UPLOADED
            metadata['processing'] = 'awaiting_ai_service'
            message = 'تم حفظ المصدر وهو جاهز للإرسال إلى خدمة الذكاء الاصطناعي.'
        metadata['processed_at'] = timezone.now().isoformat()
        source.metadata = metadata
        source.processing_error = ''
        source.save(update_fields=['status', 'extracted_text', 'processing_error', 'metadata', 'updated_at'])
        return {'success': True, 'message': message}
    except Exception:
        logger.exception("Source processing failed for source_id=%s", source.pk)
        source.status = StudentSource.Status.FAILED
        source.processing_error = 'Source processing failed.'
        source.save(update_fields=['status', 'processing_error', 'updated_at'])
        return {'success': False, 'message': 'فشلت معالجة المصدر.', 'code': 'source_processing_failed'}


TASK_BY_CHARACTER = {
    StudentSourceInteraction.Character.FAHES: AIJob.TaskType.FAHES_GENERATE_QUIZ,
    StudentSourceInteraction.Character.KHOTA: AIJob.TaskType.KHOTA_GENERATE_PLAN,
    StudentSourceInteraction.Character.RASHEED: AIJob.TaskType.RASHEED_RECOMMENDATIONS,
    StudentSourceInteraction.Character.KHOLASA: AIJob.TaskType.KHOLASA_GENERATE_SUMMARY,
    StudentSourceInteraction.Character.SADA: AIJob.TaskType.SADA_TRANSCRIBE_AUDIO,
}


def _create_character_job(user, character, *, source=None, collection=None, action=None):
    task_type = TASK_BY_CHARACTER.get(character)
    if task_type is None:
        raise ValidationError({'character': 'الشخصية غير مدعومة.'})
    if character == StudentSourceInteraction.Character.SADA and (
        collection or source is None or source.source_type != StudentSource.SourceType.AUDIO
    ):
        raise ValidationError({'character': 'صدى يعمل فقط مع مصدر صوتي.'})
    parameters = {'requested_action': action} if action else {}
    job, created = create_ai_job(
        user=user,
        task_type=task_type,
        source=source,
        collection=collection,
        subject=getattr(source, 'subject', None) or getattr(collection, 'subject', None),
        parameters=parameters,
    )
    interaction = StudentSourceInteraction.objects.filter(metadata__ai_job_id=str(job.public_id)).first()
    return {
        'success': True,
        'available': True,
        'created': created,
        'message': 'تم إنشاء الطلب وسيتم تحديث حالته عند اكتمال المعالجة.',
        'interaction': interaction,
        'ai_job': job,
    }


def use_source_with_character(user, source, character, action=None):
    if source.user_id != user.id:
        raise ValidationError('لا تملك هذا المصدر.')
    if source.status in {StudentSource.Status.PROCESSING, StudentSource.Status.FAILED}:
        raise ValidationError('المصدر غير جاهز للاستخدام مع الذكاء الاصطناعي.')
    return _create_character_job(user, character, source=source, action=action)


def use_collection_with_character(user, collection, character, action=None):
    if collection.user_id != user.id:
        raise ValidationError('لا تملك هذا المجلد.')
    usable_sources = collection.sources.filter(status__in=[StudentSource.Status.UPLOADED, StudentSource.Status.READY])
    if not usable_sources.exists() and character in {
        StudentSourceInteraction.Character.FAHES,
        StudentSourceInteraction.Character.KHOTA,
        StudentSourceInteraction.Character.KHOLASA,
        StudentSourceInteraction.Character.SADA,
    }:
        raise ValidationError('أضف مصدرًا واحدًا على الأقل إلى المجلد.')
    return _create_character_job(user, character, collection=collection, action=action)
