"""Vercel deployment: production settings adapted to serverless functions.

The function filesystem is read-only and ephemeral, so the database must be external (Neon Postgres)
and uploads go to Vercel Blob. Static files are collected at build time and served by Vercel's CDN.
"""

from django.core.exceptions import ImproperlyConfigured

from .prod import *  # noqa: F403
from .prod import DATABASES, LOGGING, STORAGES, env

# Without it base.py falls back to SQLite (or a dummy backend when the value is empty, as `vercel env
# pull` writes Secret variables), and commands like `seed` would run against the wrong database.
if not env("DATABASE_URL", default="").startswith(("postgres://", "postgresql://")):
    raise ImproperlyConfigured(
        "DATABASE_URL must be the Neon Postgres URL (Vercel project > Storage > your database > .env.local)."
    )

# Instances freeze between requests, so pooled connections would go stale: connect per request and
# let Neon's pooler (PgBouncer, transaction mode) do the pooling.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0)
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

STORAGES = {**STORAGES, "default": {"BACKEND": "apps.core.storage.VercelBlobStorage"}}
# The Blob SDK's HTTP client logs every request at INFO.
LOGGING = {**LOGGING, "loggers": {**LOGGING["loggers"], "httpx2": {"level": "WARNING"}}}

# During active development/demo deployments on Vercel, allow testing login with 123456
OTP_TEST_CODE = env("OTP_TEST_CODE", default="123456")
ALLOW_OTP_TEST_CODE = env.bool("ALLOW_OTP_TEST_CODE", default=True)
