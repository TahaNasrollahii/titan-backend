"""Local development settings."""

from .base import *  # noqa: F403
from .base import env

DEBUG = env.bool("DEBUG", default=True)
OTP_TEST_CODE = env("OTP_TEST_CODE", default="1234")  # log in with 1234 while testing
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0"])
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS", default=["http://localhost:3000", "http://127.0.0.1:3000"]
)
