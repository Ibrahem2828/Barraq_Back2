import mimetypes
import zipfile
from pathlib import Path

from django.conf import settings
from rest_framework.exceptions import ValidationError

# Every extension here must be one the AI service can actually turn into text,
# because accepting an upload is a promise to process it. The authoritative
# list on the other side is Baraq_AI's DocumentExtractor.extract() dispatch
# (txt/pdf/docx/pptx) plus the Sada audio pipeline, which requires an audio/*
# mime type. jpg/jpeg/png/webp (no OCR path), and doc/ppt (legacy OLE, no
# reader) were accepted here until they were removed: uploads succeeded and
# then every AI job failed with `unsupported_source_format`. If OCR or a
# legacy-Office converter is added later, restore the entry here *and* the
# matching signature check -- both are recoverable from this commit's parent.
ALLOWED_EXTENSIONS = {
    "pdf", "txt", "docx", "pptx", "mp3", "m4a", "wav",
}
DANGEROUS_EXTENSIONS = {
    "exe", "sh", "bat", "cmd", "js", "html", "php", "py", "jar", "zip", "rar", "7z", "sql", "env",
}
EXTENSION_SOURCE_TYPES = {
    "pdf": "pdf", "txt": "text",
    "docx": "document", "pptx": "presentation",
    "mp3": "audio", "m4a": "audio", "wav": "audio",
}
SUPPORTED_FORMATS_LABEL = "PDF, TXT, DOCX, PPTX, MP3, M4A, WAV"


def get_safe_extension(filename):
    extension = Path(filename or "").suffix.lower().lstrip(".")
    if not extension:
        raise ValidationError({"file": "تعذر تحديد نوع الملف. تأكد أن الملف يحتوي على امتداد صحيح."})
    if extension in DANGEROUS_EXTENSIONS:
        raise ValidationError({"file": f"هذا النوع من الملفات غير مسموح به: .{extension}"})
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            {"file": f"نوع الملف غير مدعوم: .{extension}. الصيغ المدعومة: {SUPPORTED_FORMATS_LABEL}."}
        )
    return extension


def get_file_mime_type(file):
    content_type = getattr(file, "content_type", "") or ""
    guessed_type, _encoding = mimetypes.guess_type(getattr(file, "name", ""))
    return content_type or guessed_type or "application/octet-stream"


def _read_head(file, length=32):
    position = file.tell() if hasattr(file, "tell") else None
    try:
        file.seek(0)
        return file.read(length)
    finally:
        if hasattr(file, "seek"):
            file.seek(position or 0)


MAX_OFFICE_ENTRIES = 5000
MAX_OFFICE_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_OFFICE_COMPRESSION_RATIO = 150


def _validate_office_zip(file, extension):
    position = file.tell() if hasattr(file, "tell") else None
    try:
        file.seek(0)
        with zipfile.ZipFile(file) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_OFFICE_ENTRIES:
                raise ValidationError({'file': 'ملف Office يحتوي على عدد مفرط من العناصر.'})
            total_uncompressed = sum(item.file_size for item in infos)
            total_compressed = max(sum(item.compress_size for item in infos), 1)
            if total_uncompressed > MAX_OFFICE_UNCOMPRESSED_BYTES:
                raise ValidationError({'file': 'الحجم غير المضغوط لملف Office يتجاوز الحد الآمن.'})
            if total_uncompressed / total_compressed > MAX_OFFICE_COMPRESSION_RATIO:
                raise ValidationError({'file': 'نسبة ضغط ملف Office غير آمنة.'})
            if any('..' in Path(item.filename).parts or item.filename.startswith(('/', '\\')) for item in infos):
                raise ValidationError({'file': 'ملف Office يحتوي على مسارات غير آمنة.'})
            names = {item.filename for item in infos}
            required_prefix = "word/" if extension == "docx" else "ppt/"
            if "[Content_Types].xml" not in names or not any(name.startswith(required_prefix) for name in names):
                raise ValidationError({"file": "محتوى ملف Office لا يطابق امتداده."})
            if any(name.lower().endswith(("vbaproject.bin", ".exe", ".js", ".bat", ".cmd")) for name in names):
                raise ValidationError({"file": "ملفات Office التي تحتوي على ماكرو أو ملفات تنفيذية غير مسموحة."})
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValidationError({"file": "ملف Office تالف أو غير صالح."}) from exc
    finally:
        if hasattr(file, "seek"):
            file.seek(position or 0)


def validate_file_signature(file, extension):
    head = _read_head(file)
    valid = True
    if extension == "pdf":
        valid = head.startswith(b"%PDF-")
    elif extension == "wav":
        valid = head.startswith(b"RIFF") and head[8:12] == b"WAVE"
    elif extension == "mp3":
        valid = head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
    elif extension == "m4a":
        valid = len(head) >= 12 and head[4:8] == b"ftyp"
    elif extension in {"docx", "pptx"}:
        _validate_office_zip(file, extension)
        return
    elif extension == "txt":
        valid = b"\x00" not in head
    if not valid:
        raise ValidationError({"file": "محتوى الملف لا يطابق الامتداد المعلن."})


def get_source_type_from_file(file):
    extension = get_safe_extension(getattr(file, "name", ""))
    return EXTENSION_SOURCE_TYPES.get(extension, "other")


def validate_student_source_file(file):
    if file is None:
        raise ValidationError({"file": "يرجى اختيار ملف لرفعه."})
    size = getattr(file, "size", 0) or 0
    if size <= 0:
        raise ValidationError({"file": "الملف فارغ. يرجى اختيار ملف صالح."})
    # Settings defaults to the same 50MB ceiling as the AI ingestion service.
    # Keep this fallback aligned for isolated serializer/unit-test use too.
    max_mb = getattr(settings, "STUDENT_SOURCE_MAX_UPLOAD_MB", 50)
    if size > max_mb * 1024 * 1024:
        raise ValidationError({"file": f"حجم الملف أكبر من الحد المسموح ({max_mb}MB)."})
    extension = get_safe_extension(getattr(file, "name", ""))
    validate_file_signature(file, extension)
    return {
        "extension": extension,
        "mime_type": get_file_mime_type(file),
        "source_type": EXTENSION_SOURCE_TYPES.get(extension, "other"),
        "file_size": size,
        "original_filename": Path(getattr(file, "name", "")).name,
    }
