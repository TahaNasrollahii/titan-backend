"""Cloudflare Turnstile server-side verification."""

import logging
import requests
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile_token(token: str, ip: str | None = None) -> bool:
    """Verify a Turnstile token against Cloudflare's siteverify endpoint.

    Fails closed if a key is configured but the token is invalid or Cloudflare cannot be reached.
    If TURNSTILE_SECRET_KEY is not configured, verification is bypassed.
    """
    secret = getattr(settings, "TURNSTILE_SECRET_KEY", "").strip()
    if not secret:
        return True

    if not token:
        logger.warning("Turnstile token is missing.")
        return False

    try:
        payload = {"secret": secret, "response": token}
        if ip:
            payload["remoteip"] = ip
        response = requests.post(TURNSTILE_VERIFY_URL, data=payload, timeout=5)
        response.raise_for_status()
        data = response.json()
        if not data.get("success", False):
            logger.warning("Turnstile verification failed: %s", data.get("error-codes"))
            return False
        return True
    except Exception as exc:
        logger.exception("Failed to verify Turnstile token: %s", exc)
        return False


def validate_turnstile(request):
    """Enforces Turnstile verification on the current DRF request."""
    secret = getattr(settings, "TURNSTILE_SECRET_KEY", "").strip()
    if not secret:
        return

    token = ""
    if hasattr(request, "data") and isinstance(request.data, dict):
        token = request.data.get("turnstile_token") or request.data.get("turnstileToken") or ""
    if not token and hasattr(request, "headers"):
        token = request.headers.get("X-Turnstile-Token", "")

    # Extract client IP if possible
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    client_ip = xff.split(",")[-1].strip() if xff else request.META.get("REMOTE_ADDR")

    if not verify_turnstile_token(token, ip=client_ip):
        raise ValidationError({"detail": _("Security verification failed. Please try again.")})

