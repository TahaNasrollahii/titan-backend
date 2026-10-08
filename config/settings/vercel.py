"""Vercel deployment: production settings adapted to serverless functions.

The function filesystem is read-only and ephemeral, so the database must be external (Neon Postgres)
and uploads go to Vercel Blob. Static files are collected at build time and served by Vercel's CDN.
"""

from .prod import *  # noqa: F403
from .prod import DATABASES, STORAGES, env

# Instances freeze between requests, so pooled connections would go stale: connect per request and
# let Neon's pooler (PgBouncer, transaction mode) do the pooling.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0)
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

STORAGES = {**STORAGES, "default": {"BACKEND": "apps.core.storage.VercelBlobStorage"}}

# Unlike prod, nothing is implied: the demo login code only exists when OTP_TEST_CODE is set
# (and prod.py requires ALLOW_OTP_TEST_CODE alongside it).
OTP_TEST_CODE = env("OTP_TEST_CODE", default="")
