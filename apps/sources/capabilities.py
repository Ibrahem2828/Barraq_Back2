from .models import StudentSource


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
    return _apply_entitlements(capabilities, features)


def get_collection_character_capabilities(collection, *, features=None):
    sources = list(collection.sources.all())
    has_sources = bool(sources)
    capabilities = {
        'khota': {'available': has_sources, 'actions': ['create_study_plan'] if has_sources else [], 'message': 'إنشاء خطة من محتوى المجلد.' if has_sources else 'أضف مصادر أولاً.'},
        'fahes': {'available': has_sources, 'actions': ['create_quiz'] if has_sources else [], 'message': 'إنشاء اختبار من المجلد.' if has_sources else 'أضف مصادر أولاً.'},
        'rasheed': {'available': True, 'actions': ['study_advice'], 'message': 'تحليل الأداء المرتبط بالمجلد.'},
        'kholasa': {'available': has_sources, 'actions': ['summarize'] if has_sources else [], 'message': 'تلخيص مصادر المجلد.' if has_sources else 'أضف مصادر أولاً.'},
        # One Sada job maps to one audio object. Selecting a collection is
        # ambiguous when it contains several recordings or non-audio files.
        'sada': {'available': False, 'actions': [], 'message': 'اختر مصدراً صوتياً واحداً من المجلد قبل بدء صدى.'},
    }
    return _apply_entitlements(capabilities, features)
