"""Settings shared by every environment. Environment-specific modules override these."""

from datetime import timedelta
from pathlib import Path

import environ
from corsheaders.defaults import default_headers

from config.lazy import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# ENV_FILE points at another file, e.g. one pulled with `vercel env pull` to run commands against the demo.
environ.Env.read_env(env.str("ENV_FILE", default=str(BASE_DIR / ".env")), overwrite=False)

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

DJANGO_APPS = [
    # Unfold (admin theme) must come before django.contrib.admin so its templates win.
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
]
LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.catalog",
    "apps.payments",
    "apps.orders",
    "apps.teams",
    "apps.tournaments",
    "apps.notifications",
    "apps.content",
    "apps.support",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "apps.core.middleware.AdminEnglishMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------- i18n
LANGUAGE_CODE = "fa"
LANGUAGES = [("fa", _("Persian")), ("en", _("English"))]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------- static & media
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))
MAX_UPLOAD_SIZE = 2 * 1024 * 1024  # 2 MB, per the frontend's upload hints

# ---------------------------------------------------------------- cache
CACHES = {
    "default": {
        "BACKEND": env(
            "CACHE_BACKEND",
            default="django.core.cache.backends.db.DatabaseCache",
        ),
        "LOCATION": env("CACHE_LOCATION", default="django_cache_table"),
    }
}
if env("REDIS_URL", default=""):
    CACHES["default"] = {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
    }

# ---------------------------------------------------------------- DRF
REST_FRAMEWORK = {
    "NUM_PROXIES": 1,
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticatedOrReadOnly"],
    "DEFAULT_RENDERER_CLASSES": ["djangorestframework_camel_case.render.CamelCaseJSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "djangorestframework_camel_case.parser.CamelCaseJSONParser",
        "djangorestframework_camel_case.parser.CamelCaseMultiPartParser",
        "djangorestframework_camel_case.parser.CamelCaseFormParser",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 12,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.core.throttling.AnonRateThrottle",
        "apps.core.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="120/min"),
        "user": env("THROTTLE_USER", default="600/min"),
        "otp_ip": env("THROTTLE_OTP_IP", default="20/hour"),
        "checkout": env("THROTTLE_CHECKOUT", default="30/hour"),
        "wallet_topup": env("THROTTLE_WALLET_TOPUP", default="30/hour"),
        # Live chat. Visitors are keyed by their chat token, not IP: mobile carriers share IPs widely.
        "support_start": env("THROTTLE_SUPPORT_START", default="30/hour"),
        "support_poll": env("THROTTLE_SUPPORT_POLL", default="180/min"),
        "support_send": env("THROTTLE_SUPPORT_SEND", default="40/min"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=30)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Titan API",
    "DESCRIPTION": "REST API for the Titan gaming store & esports tournament platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "CAMELIZE_NAMES": True,
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.contrib.djangorestframework_camel_case.camelize_serializer_fields",
        "drf_spectacular.hooks.postprocess_schema_enums",
    ],
    "ENUM_NAME_OVERRIDES": {
        "PaymentMethodEnum": "apps.payments.models.PaymentMethod",
        "PaymentStatusEnum": "apps.payments.models.Payment.Status",
        "OrderStatusEnum": "apps.orders.models.Order.Status",
        "TournamentStatusEnum": "apps.tournaments.models.Tournament.Status",
        "RegistrationStatusEnum": "apps.tournaments.models.Registration.Status",
        "MatchStatusEnum": "apps.tournaments.models.Match.Status",
        "InvitationStatusEnum": "apps.teams.models.TeamInvitation.Status",
        "FriendshipStatusEnum": "apps.accounts.models.Friendship.Status",
        "ConversationStatusEnum": "apps.support.models.Conversation.Status",
        "SupportMessageKindEnum": "apps.support.models.Message.Kind",
    },
}

# ---------------------------------------------------------------- CORS / frontend
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000").rstrip("/")
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[FRONTEND_URL])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[FRONTEND_URL])
# The live-chat widget identifies visitors with this header, Turnstile with X-Turnstile-Token.
CORS_ALLOW_HEADERS = (*default_headers, "x-support-token", "x-turnstile-token")

# ---------------------------------------------------------------- domain settings
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")

OTP_LENGTH = 6
OTP_TTL_SECONDS = env.int("OTP_TTL_SECONDS", default=120)
OTP_MAX_ATTEMPTS = 3
OTP_RESEND_COOLDOWN_SECONDS = env.int("OTP_RESEND_COOLDOWN_SECONDS", default=60)
OTP_MAX_PER_HOUR = env.int("OTP_MAX_PER_HOUR", default=5)
OTP_MAX_PER_DAY = env.int("OTP_MAX_PER_DAY", default=15)
SMS_GLOBAL_HOURLY_CAP = env.int("SMS_GLOBAL_HOURLY_CAP", default=100)
TURNSTILE_SECRET_KEY = env("TURNSTILE_SECRET_KEY", default="")
# Testing only: when set, every OTP is this fixed code. Must stay empty in production.
OTP_TEST_CODE = env("OTP_TEST_CODE", default="")
ALLOW_OTP_TEST_CODE = env.bool("ALLOW_OTP_TEST_CODE", default=False)

SMS_BACKEND = env("SMS_BACKEND", default="apps.accounts.sms.ConsoleSMSBackend")
KAVENEGAR_API_KEY = env("KAVENEGAR_API_KEY", default="")
KAVENEGAR_OTP_TEMPLATE = env("KAVENEGAR_OTP_TEMPLATE", default="titan-otp")
# Kavenegar templates with one token (the ticket, e.g. T-1042) for live-chat texts.
KAVENEGAR_SUPPORT_ALERT_TEMPLATE = env("KAVENEGAR_SUPPORT_ALERT_TEMPLATE", default="titan-support-alert")
KAVENEGAR_SUPPORT_REPLY_TEMPLATE = env("KAVENEGAR_SUPPORT_REPLY_TEMPLATE", default="titan-support-reply")

PAYMENT_GATEWAY = env("PAYMENT_GATEWAY", default="zarinpal")
ZARINPAL_MERCHANT_ID = env("ZARINPAL_MERCHANT_ID", default="")
ZARINPAL_SANDBOX = env.bool("ZARINPAL_SANDBOX", default=True)
PAYMENT_CALLBACK_URL = env("PAYMENT_CALLBACK_URL", default="http://localhost:8000/api/v1/payments/callback/")
PAYMENT_RESULT_PATH = "/payment/result"
WALLET_TOPUP_MIN = 10_000
WALLET_TOPUP_MAX = 50_000_000

PRESENCE_TIMEOUT_SECONDS = 120

# Live support chat
# The desk polls every few seconds, but browsers slow timers in background tabs to about one a minute,
# so an agent with the desk in another tab still counts as online for this long.
SUPPORT_AGENT_TIMEOUT_SECONDS = 150
SUPPORT_VISITOR_TIMEOUT_SECONDS = 30
SUPPORT_TYPING_SECONDS = 6
SUPPORT_MESSAGE_MAX_LENGTH = 2000
SUPPORT_ALERT_COOLDOWN_SECONDS = env.int("SUPPORT_ALERT_COOLDOWN_SECONDS", default=600)
SEED_ADMIN_PASSWORD = env("SEED_ADMIN_PASSWORD", default="")

# ---------------------------------------------------------------- logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

# ---------------------------------------------------------------- admin theme
from .unfold import UNFOLD  # noqa: E402

UNFOLD["SITE_URL"] = FRONTEND_URL  # "Return to site" goes to the storefront, not the API root.

# Compact English date formats for the admin (the public API serialises ISO-8601 regardless).
FORMAT_MODULE_PATH = ["config.formats"]
