"""Production settings. Every secret must come from the environment."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import LOGGING, MIDDLEWARE, env

DEBUG = False
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

# A fixed login code is a backdoor. Only throwaway demo deployments may opt in, explicitly.
if env("OTP_TEST_CODE", default="") and not env.bool("ALLOW_OTP_TEST_CODE", default=False):
    raise ImproperlyConfigured("OTP_TEST_CODE must not be set in production.")

# Fake payment gateway auto-approves for demo/testing unless explicitly disallowed
if env("PAYMENT_GATEWAY", default="zarinpal") == "fake" and env.bool("REQUIRE_REAL_PAYMENT", default=False):
    raise ImproperlyConfigured("PAYMENT_GATEWAY='fake' is forbidden when REQUIRE_REAL_PAYMENT=True.")

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
