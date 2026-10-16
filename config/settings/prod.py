"""Production settings. Every secret must come from the environment."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import LOGGING, MIDDLEWARE, env

DEBUG = False
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

# ---------------------------------------------------------------- Security safeguards
# OTP Test code is strictly forbidden in production (no bypass flag permitted)
if env("OTP_TEST_CODE", default=""):
    raise ImproperlyConfigured("OTP_TEST_CODE is strictly forbidden in production.")

OTP_TEST_CODE = ""
ALLOW_OTP_TEST_CODE = False

# Fake payment gateway is strictly forbidden in production
if env("PAYMENT_GATEWAY", default="zarinpal").lower() == "fake":
    raise ImproperlyConfigured("PAYMENT_GATEWAY='fake' is strictly forbidden in production.")

PAYMENT_GATEWAY = "zarinpal"

# Zarinpal sandbox mode is strictly forbidden in production
if env.bool("ZARINPAL_SANDBOX", default=False):
    raise ImproperlyConfigured("ZARINPAL_SANDBOX=True is strictly forbidden in production.")

ZARINPAL_SANDBOX = False

# Real merchant ID must be configured in production
_zarinpal_merchant = env("ZARINPAL_MERCHANT_ID", default="").strip()
if not _zarinpal_merchant or _zarinpal_merchant == "00000000-0000-0000-0000-000000000000":
    raise ImproperlyConfigured("A valid ZARINPAL_MERCHANT_ID must be configured in production.")

ZARINPAL_MERCHANT_ID = _zarinpal_merchant

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 365)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"

# WhiteNoise serves the collected static files (admin, API docs) right after the security middleware.
MIDDLEWARE = [MIDDLEWARE[0], "whitenoise.middleware.WhiteNoiseMiddleware", *MIDDLEWARE[1:]]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

LOGGING = {
    **LOGGING,
    "formatters": {
        "json": {
            "format": (
                '{{"time": "{asctime}", "level": "{levelname}", "logger": "{name}", "message": "{message}"}}'
            ),
            "style": "{",
        },
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
}
