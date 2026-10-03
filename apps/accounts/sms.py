"""Pluggable SMS delivery. Select the backend with the ``SMS_BACKEND`` setting."""

import logging
from functools import cache

from django.conf import settings
from django.utils.module_loading import import_string

import requests

from apps.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


class BaseSMSBackend:
    def send_otp(self, phone: str, code: str) -> None:
        raise NotImplementedError


class ConsoleSMSBackend(BaseSMSBackend):
    """Development backend: writes the code to the server log instead of sending an SMS."""

    def send_otp(self, phone: str, code: str) -> None:
        logger.warning("OTP for %s: %s", phone, code)


class InMemorySMSBackend(BaseSMSBackend):
    """Test backend: keeps every message in ``outbox`` so tests can read the code."""

    outbox: list[tuple[str, str]] = []

    def send_otp(self, phone: str, code: str) -> None:
        self.outbox.append((phone, code))

    @classmethod
    def last_code_for(cls, phone: str) -> str | None:
        return next((code for to, code in reversed(cls.outbox) if to == phone), None)


class KavenegarSMSBackend(BaseSMSBackend):
    """Sends codes through Kavenegar's template-based ``verify/lookup`` API."""

    timeout_seconds = 10

    def send_otp(self, phone: str, code: str) -> None:
        url = f"https://api.kavenegar.com/v1/{settings.KAVENEGAR_API_KEY}/verify/lookup.json"
        params = {"receptor": phone, "token": code, "template": settings.KAVENEGAR_OTP_TEMPLATE}
        try:
            response = requests.post(url, data=params, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("Kavenegar OTP delivery failed for %s", phone)
            raise ExternalServiceError() from exc


@cache
def get_sms_backend() -> BaseSMSBackend:
    return import_string(settings.SMS_BACKEND)()
