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

    def send_support_alert(self, phone: str, ticket: str) -> None:
        """Tell a support agent that a visitor is waiting in live chat."""
        raise NotImplementedError

    def send_support_reply(self, phone: str, ticket: str) -> None:
        """Tell a visitor who left the page that support has answered."""
        raise NotImplementedError


class ConsoleSMSBackend(BaseSMSBackend):
    """Development backend: writes the code to the server log instead of sending an SMS."""

    def send_otp(self, phone: str, code: str) -> None:
        logger.warning("OTP for %s: %s", phone, code)

    def send_support_alert(self, phone: str, ticket: str) -> None:
        logger.warning("Support alert for %s: visitor waiting in %s", phone, ticket)

    def send_support_reply(self, phone: str, ticket: str) -> None:
        logger.warning("Support reply notice for %s: %s answered", phone, ticket)


class InMemorySMSBackend(BaseSMSBackend):
    """Test backend: keeps every message in ``outbox`` so tests can read the code."""

    outbox: list[tuple[str, str]] = []
    # Non-OTP texts as (kind, phone, ticket), kept apart so ``last_code_for`` only ever sees codes.
    notices: list[tuple[str, str, str]] = []

    def send_otp(self, phone: str, code: str) -> None:
        self.outbox.append((phone, code))

    def send_support_alert(self, phone: str, ticket: str) -> None:
        self.notices.append(("support_alert", phone, ticket))

    def send_support_reply(self, phone: str, ticket: str) -> None:
        self.notices.append(("support_reply", phone, ticket))

    @classmethod
    def last_code_for(cls, phone: str) -> str | None:
        return next((code for to, code in reversed(cls.outbox) if to == phone), None)


class KavenegarSMSBackend(BaseSMSBackend):
    """Sends codes through Kavenegar's template-based ``verify/lookup`` API."""

    timeout_seconds = 10

    def send_otp(self, phone: str, code: str) -> None:
        self._lookup(phone, code, settings.KAVENEGAR_OTP_TEMPLATE)

    def send_support_alert(self, phone: str, ticket: str) -> None:
        self._lookup(phone, ticket, settings.KAVENEGAR_SUPPORT_ALERT_TEMPLATE)

    def send_support_reply(self, phone: str, ticket: str) -> None:
        self._lookup(phone, ticket, settings.KAVENEGAR_SUPPORT_REPLY_TEMPLATE)

    def _lookup(self, phone: str, token: str, template: str) -> None:
        url = f"https://api.kavenegar.com/v1/{settings.KAVENEGAR_API_KEY}/verify/lookup.json"
        params = {"receptor": phone, "token": token, "template": template}
        try:
            response = requests.post(url, data=params, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("Kavenegar delivery (%s) failed for %s", template, phone)
            raise ExternalServiceError() from exc


@cache
def get_sms_backend() -> BaseSMSBackend:
    return import_string(settings.SMS_BACKEND)()
