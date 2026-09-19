from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

from apps.common.env_config import (
    resolve_list_setting,
    validate_allowed_hosts,
    validate_origin_list,
    validate_origin_url,
    validate_secret_key,
)

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    ENVIRONMENT=(str, "production"),
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    TIME_ZONE=(str, "Asia/Damascus"),
    ACCESS_TOKEN_LIFETIME_MINUTES=(int, 30),
    REFRESH_TOKEN_LIFETIME_DAYS=(int, 14),
    DATABASE_CONN_MAX_AGE=(int, 60),
    DATABASE_CONN_HEALTH_CHECKS=(bool, True),
    DATABASE_CONNECT_TIMEOUT=(int, 10),
    # Platform ceiling for one source upload. Bounded by the AI service,
    # which reads a whole source into memory during ingestion
    # (Baraq_AI settings.max_source_file_bytes = 50MB). Raising this
    # above that figure would let Django accept a file the AI must then
    # reject, so the two move together.
    STUDENT_SOURCE_MAX_UPLOAD_MB=(int, 50),
    API_DOCS_PUBLIC=(bool, False),
    AI_SERVICE_ENABLED=(bool, True),
    AI_SERVICE_VERIFY_SSL=(bool, True),
    AI_SERVICE_TIMEOUT_SECONDS=(int, 30),
    AI_SERVICE_ALLOW_INSECURE_HTTP=(bool, False),
    AI_DATASET_CONSENT_VERSION=(str, "2026-07-01"),
    PAYMENTS_ENABLED=(bool, False),
    SECURE_SSL_REDIRECT=(bool, True),
    SECURE_HSTS_SECONDS=(int, 31536000),
    SECURE_HSTS_INCLUDE_SUBDOMAINS=(bool, True),
    SECURE_HSTS_PRELOAD=(bool, False),
    CORS_ALLOW_CREDENTIALS=(bool, False),
    DATABASE_STATEMENT_TIMEOUT_MS=(int, 30000),
)
environ.Env.read_env(BASE_DIR / ".env")

ENVIRONMENT = env("ENVIRONMENT")
DEBUG = env("DEBUG")
SECRET_KEY = env("SECRET_KEY", default="")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = resolve_list_setting(
    env,
    "CSRF_TRUSTED_ORIGINS",
    fallback_keys=("DJANGO_CSRF_TRUSTED_ORIGINS",),
    default=CORS_ALLOWED_ORIGINS,
)
PUBLIC_API_BASE_URL = env("PUBLIC_API_BASE_URL", default="http://localhost:8000").rstrip("/")

if not DEBUG:
    try:
        validate_secret_key(SECRET_KEY)
        public_api_hostname = validate_origin_url(
            PUBLIC_API_BASE_URL,
            setting_name="PUBLIC_API_BASE_URL",
            require_https=True,
        )
        validate_allowed_hosts(ALLOWED_HOSTS, public_api_hostname=public_api_hostname)
        validate_origin_list(
            CORS_ALLOWED_ORIGINS,
            setting_name="CORS_ALLOWED_ORIGINS",
            require_https=True,
        )
        validate_origin_list(
            CSRF_TRUSTED_ORIGINS,
            setting_name="CSRF_TRUSTED_ORIGINS",
            require_https=True,
        )
    except ValueError as exc:
        raise ImproperlyConfigured(str(exc)) from exc
else:
    SECRET_KEY = SECRET_KEY or "django-insecure-development-only-not-for-production"
    ALLOWED_HOSTS = ALLOWED_HOSTS or ["127.0.0.1", "localhost", "testserver"]

APP_NAME = "Baraq Backend"
APP_VERSION = "4.0.0"
APP_PHASE = "4.0"
API_VERSION = "v1"
LEGACY_API_SUNSET = "Wed, 31 Dec 2026 23:59:59 GMT"
APP_FEATURES = {
    "auth": True,
    "study_plans": True,
    "quizzes": True,
    "sources": True,
    "subscriptions": True,
    "dashboard": True,
    "ai_service_integration": True,
    "fahes": True,
    "khota": True,
    "rasheed": True,
    "kholasa": True,
    "sada": True,
    "notifications": True,
    "support": True,
    "payments": env("PAYMENTS_ENABLED"),
}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "apps.common",
    "apps.users",
    "apps.students",
    "apps.subjects",
    "apps.projects.apps.ProjectsConfig",
    "apps.study_plans",
    "apps.quizzes",
    "apps.sources",
    "apps.subscriptions.apps.SubscriptionsConfig",
    "apps.ai_integration.apps.AIIntegrationConfig",
    "apps.analytics.apps.AnalyticsConfig",
    "apps.summaries.apps.SummariesConfig",
    "apps.audio.apps.AudioConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.support.apps.SupportConfig",
    "apps.waitlist.apps.WaitlistConfig",
    "apps.admin_dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.common.middleware.RequestIDMiddleware",
    "apps.common.middleware.APIVersionHeadersMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

DATABASE_URL = env("DATABASE_URL", default=f"sqlite:///{(BASE_DIR / 'db.sqlite3').as_posix()}" if DEBUG else "")
if not DATABASE_URL:
    raise ImproperlyConfigured("DATABASE_URL is required in production.")
DATABASES = {"default": env.db("DATABASE_URL", default=DATABASE_URL)}
DATABASES["default"]["CONN_MAX_AGE"] = env("DATABASE_CONN_MAX_AGE")
DATABASES["default"]["CONN_HEALTH_CHECKS"] = env("DATABASE_CONN_HEALTH_CHECKS")
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    options = DATABASES["default"].setdefault("OPTIONS", {})
    options["connect_timeout"] = env("DATABASE_CONNECT_TIMEOUT")
    options["options"] = f"-c statement_timeout={env('DATABASE_STATEMENT_TIMEOUT_MS')}"

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache" if REDIS_URL.startswith("redis") else "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": REDIS_URL if REDIS_URL.startswith("redis") else "baraq-local-cache",
        "TIMEOUT": 300,
        "KEY_PREFIX": "baraq",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Password hashes in the test database are deliberately cheap. This branch is
# reached only through ``manage.py test`` and has no effect on development or
# production authentication policies.
if "test" in sys.argv:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

AUTH_USER_MODEL = "users.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "ar"
LANGUAGES = [("ar", "العربية"), ("en", "English")]
TIME_ZONE = env("TIME_ZONE")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
}
MEDIA_URL = env("MEDIA_URL", default="/media/")
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
STUDENT_SOURCE_MAX_UPLOAD_MB = env("STUDENT_SOURCE_MAX_UPLOAD_MB")
FILE_UPLOAD_MAX_MEMORY_SIZE = min(STUDENT_SOURCE_MAX_UPLOAD_MB, 10) * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = (STUDENT_SOURCE_MAX_UPLOAD_MB + 2) * 1024 * 1024

if env.bool("USE_S3_STORAGE", default=False):
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default=None)
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    AWS_S3_FILE_OVERWRITE = False

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework_simplejwt.authentication.JWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.common.exceptions.custom_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ("apps.common.renderers.EnvelopeJSONRenderer",),
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="60/hour"),
        "user": env("THROTTLE_USER", default="2000/day"),
        "register": env("THROTTLE_REGISTER", default="10/hour"),
        "login": env("THROTTLE_LOGIN", default="10/minute"),
        "password_reset": env("THROTTLE_PASSWORD_RESET", default="5/hour"),
        "email_otp_verify": env("THROTTLE_EMAIL_OTP_VERIFY", default="20/hour"),
        "email_otp_resend": env("THROTTLE_EMAIL_OTP_RESEND", default="5/hour"),
        "uploads": env("THROTTLE_UPLOADS", default="30/hour"),
        "ai_requests": env("THROTTLE_AI", default="100/day"),
        "waitlist": env("THROTTLE_WAITLIST", default="5/hour"),
    },
}

# Only applications installed by this project are part of the default test
# suite. See apps.common.test_runner for why legacy, uninstalled prototypes
# are intentionally excluded from an unlabeled ``manage.py test`` run.
TEST_RUNNER = "apps.common.test_runner.EnabledAppsDiscoverRunner"

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env("ACCESS_TOKEN_LIFETIME_MINUTES")),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env("REFRESH_TOKEN_LIFETIME_DAYS")),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "LEEWAY": 30,
}

API_DOCS_PUBLIC = env("API_DOCS_PUBLIC")
SPECTACULAR_SETTINGS = {
    "TITLE": "Baraq Backend API",
    "DESCRIPTION": "Production API for Baraq mobile, dashboard, and AI service integration.",
    "VERSION": APP_VERSION,
    "SERVERS": [{"url": PUBLIC_API_BASE_URL, "description": "Canonical production API origin"}],
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny" if API_DOCS_PUBLIC else "rest_framework.permissions.IsAdminUser"],
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "StudyPlanStatusEnum": "apps.study_plans.models.StudyPlan.Status",
        "StudyPlanDifficultyLevelEnum": "apps.study_plans.models.StudyPlan.DifficultyLevel",
        "StudyPlanGenerationTypeEnum": "apps.study_plans.models.StudyPlan.GenerationType",
        "StudyTaskStatusEnum": "apps.study_plans.models.StudyTask.Status",
        "StudyTaskPriorityEnum": "apps.study_plans.models.StudyTask.Priority",
        "QuizTypeEnum": "apps.quizzes.models.QuizTypeChoices",
        "QuizStatusEnum": "apps.quizzes.models.QuizStatusChoices",
        "QuestionTypeEnum": "apps.quizzes.models.QuestionTypeChoices",
        "AttemptStatusEnum": "apps.quizzes.models.AttemptStatusChoices",
        "AIJobCharacterEnum": "apps.ai_integration.models.AIJob.Character",
        "AIJobStatusEnum": "apps.ai_integration.models.AIJob.Status",
        "StudentSourceStatusEnum": "apps.sources.models.StudentSource.Status",
        "StudentSourceCollectionStatusEnum": "apps.sources.models.StudentSourceCollection.Status",
        "StudentSourceInteractionCharacterEnum": "apps.sources.models.StudentSourceInteraction.Character",
        "StudentSourceInteractionStatusEnum": "apps.sources.models.StudentSourceInteraction.Status",
        "UserSubscriptionStatusEnum": "apps.subscriptions.models.UserSubscription.Status",
        "NotificationCategoryEnum": "apps.notifications.models.Notification.Category",
        "SupportTicketCategoryEnum": "apps.support.models.SupportTicket.Category",
        "SupportTicketPriorityEnum": "apps.support.models.SupportTicket.Priority",
        "SupportTicketStatusEnum": "apps.support.models.SupportTicket.Status",
    },
}

CORS_ALLOW_CREDENTIALS = env("CORS_ALLOW_CREDENTIALS")
CORS_URLS_REGEX = r"^/api/.*$"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT") if not DEBUG else False
# Container health probes reach the app over plain HTTP on loopback, bypassing
# the gateway that sets X-Forwarded-Proto. Without this exemption
# SecurityMiddleware 301s them -- and `curl --fail` treats a 301 as success,
# so the probe passed while never reaching the application at all, and every
# probe logged a redirect. These three endpoints expose only liveness,
# readiness and the app version; nothing that needs transport protection.
# Both the canonical /api/v1/ routes and the legacy /api/ aliases are served,
# and a probe may be pointed at either.
SECURE_REDIRECT_EXEMPT = [r"^api/(v1/)?health/(live/|ready/)?$"]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS") if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = env("SECURE_HSTS_INCLUDE_SUBDOMAINS") if not DEBUG else False
SECURE_HSTS_PRELOAD = env("SECURE_HSTS_PRELOAD") if not DEBUG else False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL.replace("/0", "/1"))
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 15 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 14 * 60
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
# Queue separation: latency-sensitive user-facing email (OTP, password reset)
# must never sit behind a burst of AI-dispatch or source-processing work on a
# shared queue. `critical` gets its own dedicated worker (see
# `backend-worker-critical` in compose.yaml); everything else -- including
# any future task with no explicit route -- lands on `default`.
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "users.send_email_otp": {"queue": "critical"},
    "users.send_password_reset_email": {"queue": "critical"},
}
# Durable-dispatch safety net (spec: "Accepted job loss: 0"). Requires a
# `celery beat` process running alongside the worker(s) in every deployment.
CELERY_BEAT_SCHEDULE = {
    "ai-integration-reconcile-stuck-jobs": {
        "task": "ai_integration.reconcile_stuck_jobs",
        "schedule": env.int("AI_RECONCILE_INTERVAL_SECONDS", default=60),
    },
}

AI_SERVICE_ENABLED = env("AI_SERVICE_ENABLED")
AI_SERVICE_BASE_URL = env("AI_SERVICE_BASE_URL", default="http://ai-service:8001")
AI_SERVICE_JOBS_PATH = env("AI_SERVICE_JOBS_PATH", default="/api/ai/v1/jobs")
AI_SERVICE_FEEDBACK_PATH = env("AI_SERVICE_FEEDBACK_PATH", default="/api/ai/v1/feedback")
AI_SERVICE_HEALTH_PATH = env("AI_SERVICE_HEALTH_PATH", default="/api/ai/v1/health/ready")
AI_SERVICE_VERIFY_SSL = env("AI_SERVICE_VERIFY_SSL")
AI_SERVICE_TIMEOUT_SECONDS = env("AI_SERVICE_TIMEOUT_SECONDS")
AI_SERVICE_ALLOW_INSECURE_HTTP = env("AI_SERVICE_ALLOW_INSECURE_HTTP")
# Durable dispatch-outbox tuning (apps.ai_integration.models.AIJobDispatchOutbox).
AI_DISPATCH_LOCK_TIMEOUT_SECONDS = env.int("AI_DISPATCH_LOCK_TIMEOUT_SECONDS", default=120)
AI_DISPATCH_MAX_ATTEMPTS = env.int("AI_DISPATCH_MAX_ATTEMPTS", default=8)
AI_DISPATCH_RETRY_BASE_SECONDS = env.int("AI_DISPATCH_RETRY_BASE_SECONDS", default=30)
AI_DATASET_CONSENT_VERSION = env("AI_DATASET_CONSENT_VERSION")
PAYMENTS_ENABLED = env("PAYMENTS_ENABLED")
BARAQ_SERVICE_ID = env("BARAQ_SERVICE_ID", default="baraq-django")
BARAQ_HMAC_CURRENT_KEY_ID = env("BARAQ_HMAC_CURRENT_KEY_ID", default="django-dev-1")
BARAQ_HMAC_KEYS_JSON = env(
    "BARAQ_HMAC_KEYS_JSON",
    default='{"django-dev-1":"local-development-key-not-for-production"}',
)
BARAQ_HMAC_ALLOWED_SERVICES = resolve_list_setting(
    env,
    "BARAQ_HMAC_ALLOWED_SERVICES",
    default=["baraq-ai-service"],
)
BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS = env.int("BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS", default=300)
BARAQ_HMAC_NONCE_TTL_SECONDS = env.int("BARAQ_HMAC_NONCE_TTL_SECONDS", default=600)

if BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS < 1 or BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS > 3600:
    raise ImproperlyConfigured("BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS must be between 1 and 3600.")
if BARAQ_HMAC_NONCE_TTL_SECONDS < 1 or BARAQ_HMAC_NONCE_TTL_SECONDS > 7200:
    raise ImproperlyConfigured("BARAQ_HMAC_NONCE_TTL_SECONDS must be between 1 and 7200.")
try:
    _baraq_hmac_keyring = json.loads(BARAQ_HMAC_KEYS_JSON)
except json.JSONDecodeError as exc:
    raise ImproperlyConfigured("BARAQ_HMAC_KEYS_JSON must be a JSON object.") from exc
if (
    not isinstance(_baraq_hmac_keyring, dict)
    or not _baraq_hmac_keyring
    or BARAQ_HMAC_CURRENT_KEY_ID not in _baraq_hmac_keyring
    or any(not isinstance(key_id, str) or not isinstance(secret, str) or not secret for key_id, secret in _baraq_hmac_keyring.items())
):
    raise ImproperlyConfigured("BARAQ_HMAC_KEYS_JSON must contain the current non-empty key.")
if AI_SERVICE_ENABLED and not DEBUG and (
    any(len(secret) < 32 or secret == "local-development-key-not-for-production" for secret in _baraq_hmac_keyring.values())
    or BARAQ_SERVICE_ID != "baraq-django"
    or BARAQ_HMAC_ALLOWED_SERVICES != ["baraq-ai-service"]
):
    raise ImproperlyConfigured("Production requires the approved Baraq HMAC V2 service and keyring configuration.")
if AI_SERVICE_ENABLED:
    try:
        validate_origin_url(
            AI_SERVICE_BASE_URL,
            setting_name="AI_SERVICE_BASE_URL",
            require_https=not DEBUG and not AI_SERVICE_ALLOW_INSECURE_HTTP,
        )
    except ValueError as exc:
        raise ImproperlyConfigured(str(exc)) from exc
    if not DEBUG and AI_SERVICE_BASE_URL.lower().startswith("http://") and AI_SERVICE_VERIFY_SSL:
        raise ImproperlyConfigured(
            "AI_SERVICE_VERIFY_SSL must be False when AI_SERVICE_BASE_URL uses HTTP."
        )

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be enabled.")
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Baraq <no-reply@baraq.app>")
FRONTEND_PASSWORD_RESET_URL = env("FRONTEND_PASSWORD_RESET_URL", default="baraq://reset-password")
PASSWORD_RESET_TIMEOUT = env.int("PASSWORD_RESET_TIMEOUT", default=3600)

SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(dsn=SENTRY_DSN, environment=ENVIRONMENT, traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.05), send_default_pii=False)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "apps.common.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console"], "level": env("APP_LOG_LEVEL", default="INFO"), "propagate": False},
    },
}
