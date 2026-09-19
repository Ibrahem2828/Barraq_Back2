from decimal import Decimal

FREE_PLAN_CODE = 'free'

BILLING_INTERVAL_FREE = 'free'
BILLING_INTERVAL_MONTHLY = 'monthly'
BILLING_INTERVAL_YEARLY = 'yearly'
BILLING_INTERVAL_LIFETIME = 'lifetime'
BILLING_INTERVAL_CUSTOM = 'custom'

CHARACTER_LIMIT_KEYS = {
    'khota': 'max_khota_requests_per_month',
    'fahes': 'max_fahes_requests_per_month',
    'rasheed': 'max_rasheed_requests_per_month',
    'kholasa': 'max_kholasa_requests_per_month',
    'sada': 'max_sada_requests_per_month',
}

CHARACTER_USAGE_FIELDS = {
    'khota': 'khota_requests',
    'fahes': 'fahes_requests',
    'rasheed': 'rasheed_requests',
    'kholasa': 'kholasa_requests',
    'sada': 'sada_requests',
}

CHARACTER_FEATURE_KEYS = {
    'khota': 'can_use_khota',
    'fahes': 'can_use_fahes',
    'rasheed': 'can_use_rasheed',
    'kholasa': 'can_use_kholasa',
    'sada': 'can_use_sada',
}

DEFAULT_PLANS = {
    'free': {
        'name': 'الخطة المجانية',
        'description': 'خطة بداية مجانية لاستخدام برّاق الأساسي.',
        'price': Decimal('0.00'),
        'currency': 'USD',
        'billing_interval': BILLING_INTERVAL_FREE,
        'is_public': True,
        'sort_order': 10,
        'limits': {
            'max_collections': 3,
            'max_sources': 20,
            'max_file_size_mb': 10,
            'max_storage_mb': 200,
            'max_ai_requests_per_month': 20,
            'max_khota_requests_per_month': 10,
            'max_fahes_requests_per_month': 10,
            'max_rasheed_requests_per_month': 20,
            'max_kholasa_requests_per_month': 0,
            'max_sada_requests_per_month': 0,
        },
        'features': {
            'can_use_khota': True,
            'can_use_fahes': True,
            'can_use_rasheed': True,
            'can_use_kholasa': False,
            'can_use_sada': False,
            'can_upload_audio': False,
            'can_create_unlimited_collections': False,
        },
    },
    'premium': {
        'name': 'الخطة المميزة',
        'description': 'خطة موسعة للطلاب النشطين.',
        'price': Decimal('9.99'),
        'currency': 'USD',
        'billing_interval': BILLING_INTERVAL_MONTHLY,
        'is_public': True,
        'sort_order': 20,
        'limits': {
            'max_collections': 50,
            'max_sources': 500,
            'max_file_size_mb': 50,
            'max_storage_mb': 5000,
            'max_ai_requests_per_month': 1000,
            'max_khota_requests_per_month': 300,
            'max_fahes_requests_per_month': 300,
            'max_rasheed_requests_per_month': 400,
            'max_kholasa_requests_per_month': 100,
            'max_sada_requests_per_month': 50,
        },
        'features': {
            'can_use_khota': True,
            'can_use_fahes': True,
            'can_use_rasheed': True,
            'can_use_kholasa': True,
            'can_use_sada': True,
            'can_upload_audio': True,
            'can_create_unlimited_collections': False,
        },
    },
    'pro': {
        'name': 'الخطة الاحترافية',
        'description': 'خطة عالية الحدود للاستخدام المكثف.',
        'price': Decimal('19.99'),
        'currency': 'USD',
        'billing_interval': BILLING_INTERVAL_MONTHLY,
        'is_public': True,
        'sort_order': 30,
        'limits': {
            'max_collections': 200,
            'max_sources': 3000,
            'max_file_size_mb': 50,
            'max_storage_mb': 50000,
            'max_ai_requests_per_month': 10000,
            'max_khota_requests_per_month': 3000,
            'max_fahes_requests_per_month': 3000,
            'max_rasheed_requests_per_month': 4000,
            'max_kholasa_requests_per_month': 1500,
            'max_sada_requests_per_month': 1000,
        },
        'features': {
            'can_use_khota': True,
            'can_use_fahes': True,
            'can_use_rasheed': True,
            'can_use_kholasa': True,
            'can_use_sada': True,
            'can_upload_audio': True,
            'can_create_unlimited_collections': False,
        },
    },
    'school': {
        'name': 'خطة المؤسسات التعليمية',
        'description': 'خطة مخصصة للمدارس والمؤسسات.',
        'price': Decimal('0.00'),
        'currency': 'USD',
        'billing_interval': BILLING_INTERVAL_CUSTOM,
        'is_public': False,
        'sort_order': 40,
        'limits': {
            'max_collections': 1000,
            'max_sources': 50000,
            'max_file_size_mb': 50,
            'max_storage_mb': 500000,
            'max_ai_requests_per_month': 100000,
            'max_khota_requests_per_month': 25000,
            'max_fahes_requests_per_month': 25000,
            'max_rasheed_requests_per_month': 25000,
            'max_kholasa_requests_per_month': 15000,
            'max_sada_requests_per_month': 10000,
        },
        'features': {
            'can_use_khota': True,
            'can_use_fahes': True,
            'can_use_rasheed': True,
            'can_use_kholasa': True,
            'can_use_sada': True,
            'can_upload_audio': True,
            'can_create_unlimited_collections': True,
        },
    },
}
