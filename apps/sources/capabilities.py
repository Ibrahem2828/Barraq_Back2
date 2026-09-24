from .models import StudentSource

#: Why a source cannot be used with any character right now, keyed by status.
#: Only statuses outside StudentSource.AI_USABLE_STATUSES appear here --
#: UPLOADED and READY are both usable (UPLOADED is the terminal success state
#: for every non-text source, awaiting AI-side extraction).
_UNUSABLE_STATUS_MESSAGES = {
    StudentSource.Status.FAILED: 'تعذّرت معالجة هذا المصدر. أعد المحاولة أو ارفع الملف مرة أخرى.',
    StudentSource.Status.PROCESSING: 'المصدر قيد المعالجة. حاول بعد قليل.',
}
_UNUSABLE_FALLBACK_MESSAGE = 'هذا المصدر غير جاهز للاستخدام مع الشخصيات.'


def _block_all(capabilities, message):
    return {
        character: {**capability, 'available': False, 'actions': [], 'message': message}
        for character, capability in capabilities.items()
    }


_UNSUPPORTED_TYPE_MESSAGE = (
    'هذا النوع من الملفات غير مدعوم للمعالجة بالذكاء الاصطناعي. '
    'الصيغ المدعومة: PDF, TXT, DOCX, PPTX, MP3, M4A, WAV.'
)


def _apply_source_type(capabilities, source):
    """Withhold every character for a source the AI cannot read.

    Audio is handled by the per-character rules above (Sada only). Anything
    else outside AI_EXTRACTABLE_SOURCE_TYPES -- images, links, `other` --
    cannot be turned into text by any pipeline, so advertising an action
    would guarantee an `unsupported_source_format` failure. New uploads of
    those types are rejected at the validator, but rows created before that
    allowlist was narrowed still exist and must not mislead.
    """

    if source.source_type == StudentSource.SourceType.AUDIO:
        return capabilities
    if source.source_type in StudentSource.AI_EXTRACTABLE_SOURCE_TYPES:
        return capabilities
    return _block_all(capabilities, _UNSUPPORTED_TYPE_MESSAGE)


def _apply_source_state(capabilities, source):
    """Withhold every character while the source itself is unusable.

    use_source_with_character() rejects a source outside
    AI_USABLE_STATUSES for *every* character, Rasheed included -- so
    advertising any action here for such a source guarantees the next
    request fails validation. Applied before entitlements so a plan-gated
    character still reports the (more fundamental) subscription reason.
    """

    if source.status in StudentSource.AI_USABLE_STATUSES:
        return capabilities
    return _block_all(
        capabilities,
        _UNUSABLE_STATUS_MESSAGES.get(source.status, _UNUSABLE_FALLBACK_MESSAGE),
    )


#: Khota and Fahes save their output as a StudyPlan / Quiz, and both of those
#: require a subject. Without one, Khota is refused and Fahes would spend a
#: provider call on a quiz that can never be saved.
SUBJECT_REQUIRED_MESSAGES = {
    'khota': 'حدّد مادة المشروع أولًا لإنشاء خطة دراسة.',
    'fahes': 'حدّد مادة المشروع أولًا لإنشاء اختبار.',
}


def has_subject(*owners):
    """True when any of source/collection/project carries a subject.

    Reads the foreign-key id only, so it never costs a query.
    """
    return any(getattr(owner, 'subject_id', None) for owner in owners if owner is not None)


def _apply_subject_requirement(capabilities, *owners):
    if has_subject(*owners):
        return capabilities
    for character, message in SUBJECT_REQUIRED_MESSAGES.items():
        if capabilities[character]['available']:
            capabilities[character] = {
                **capabilities[character],
                'available': False,
                'actions': [],
                'message': message,
            }
    return capabilities


def _apply_entitlements(capabilities, features):
    """Keep source capability hints aligned with the user's subscription."""

    if features is None:
        return capabilities
    for character, feature_key in {
        'khota': 'can_use_khota',
        'fahes': 'can_use_fahes',
        'rasheed': 'can_use_rasheed',
        'kholasa': 'can_use_kholasa',
        'sada': 'can_use_sada',
    }.items():
        if not features.get(feature_key, False):
            capabilities[character] = {
                **capabilities[character],
                'available': False,
                'actions': [],
                'message': 'هذه الشخصية غير متاحة في خطتك الحالية.',
            }
    return capabilities


def get_source_character_capabilities(source, *, features=None):
    is_audio = source.source_type == StudentSource.SourceType.AUDIO
    capabilities = {
        'khota': {'available': not is_audio, 'actions': ['create_study_plan'] if not is_audio else [], 'message': 'تحويل المصدر إلى خطة دراسة ذكية.' if not is_audio else 'استخدم صدى أولاً لتحويل الصوت إلى نص.'},
        'fahes': {'available': not is_audio, 'actions': ['create_quiz'] if not is_audio else [], 'message': 'إنشاء اختبار موثق من المصدر.' if not is_audio else 'استخدم صدى أولاً لتحويل الصوت إلى نص.'},
        'rasheed': {'available': True, 'actions': ['study_advice'], 'message': 'تحليل الأداء وتقديم توصيات عملية.'},
        'kholasa': {'available': not is_audio, 'actions': ['summarize'] if not is_audio else [], 'message': 'إنشاء ملخص متعدد المستويات مع مراجع.' if not is_audio else 'استخدم صدى أولاً لتفريغ التسجيل.'},
        'sada': {'available': is_audio, 'actions': ['transcribe'] if is_audio else [], 'message': 'تحويل التسجيل الصوتي إلى نص منظم.' if is_audio else 'صدى مخصص للمصادر الصوتية.'},
    }
    capabilities = _apply_source_type(capabilities, source)
    capabilities = _apply_subject_requirement(
        _apply_source_state(capabilities, source), source, source.collection, source.project
    )
    return _apply_entitlements(capabilities, features)


def get_collection_character_capabilities(collection, *, features=None):
    # Filtered in Python, not with .filter(): StudentSourceCollectionViewSet
    # prefetches `sources`, and a queryset filter here would discard that
    # cache and issue one extra query per collection on the list endpoint.
    sources = list(collection.sources.all())
    usable_sources = [
        source for source in sources if source.status in StudentSource.AI_USABLE_STATUSES
    ]
    # use_collection_with_character() requires at least one *usable* source,
    # so a folder holding only failed sources must not advertise actions.
    has_sources = bool(usable_sources)
    empty_message = (
        'أضف مصادر أولاً.'
        if not sources
        else 'لا يوجد مصدر جاهز في هذا المجلد. تحقّق من حالة المصادر.'
    )
    capabilities = {
        'khota': {'available': has_sources, 'actions': ['create_study_plan'] if has_sources else [], 'message': 'إنشاء خطة من محتوى المجلد.' if has_sources else empty_message},
        'fahes': {'available': has_sources, 'actions': ['create_quiz'] if has_sources else [], 'message': 'إنشاء اختبار من المجلد.' if has_sources else empty_message},
        'rasheed': {'available': True, 'actions': ['study_advice'], 'message': 'تحليل الأداء المرتبط بالمجلد.'},
        'kholasa': {'available': has_sources, 'actions': ['summarize'] if has_sources else [], 'message': 'تلخيص مصادر المجلد.' if has_sources else empty_message},
        # One Sada job maps to one audio object. Selecting a collection is
        # ambiguous when it contains several recordings or non-audio files.
        'sada': {'available': False, 'actions': [], 'message': 'اختر مصدراً صوتياً واحداً من المجلد قبل بدء صدى.'},
    }
    capabilities = _apply_subject_requirement(capabilities, collection, collection.project)
    return _apply_entitlements(capabilities, features)
