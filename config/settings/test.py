"""Settings for the automated test-suite: fast, isolated and deterministic."""

import os

os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-production")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "rG0nTq1Yk1C0D8vMZx0qkqk9V9w0eQn7mQm3QxQb3eA=")

from .base import *  # noqa: F403
from .base import BASE_DIR, REST_FRAMEWORK, env

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

DATABASES = {"default": env.db("TEST_DATABASE_URL", default="sqlite://:memory:")}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MEDIA_ROOT = BASE_DIR / ".test-media"

SMS_BACKEND = "apps.accounts.sms.InMemorySMSBackend"
OTP_TEST_CODE = ""  # tests exercise real random codes
PAYMENT_GATEWAY = "zarinpal"
ZARINPAL_MERCHANT_ID = "00000000-0000-0000-0000-000000000000"
ZARINPAL_SANDBOX = True

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"anon": "10000/min", "user": "10000/min", "otp_ip": "10000/min"},
}
LOGGING = {"version": 1, "disable_existing_loggers": False, "root": {"level": "CRITICAL"}}
