"""A minimal Telegram Bot API client: just the calls Titan makes, with timeouts and typed failures."""

import logging

from django.conf import settings

import requests

logger = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"


class TelegramError(Exception):
    def __init__(self, description: str, *, status: int = 0, retry_after: int | None = None):
        super().__init__(description)
        self.status = status
        self.retry_after = retry_after


class BotBlocked(TelegramError):
    """The user blocked the bot, deleted their account, or never started a chat with it (HTTP 403)."""


def is_configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN)


def call(method: str, **params) -> dict | list | bool:
    """Call a Bot API method and return its ``result``. Raises TelegramError on any failure."""
    if not is_configured():
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set.")
    url = f"{API_ROOT}/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    payload = {key: value for key, value in params.items() if value is not None}
    try:
        response = requests.post(url, json=payload, timeout=settings.TELEGRAM_API_TIMEOUT)
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        # Never log the URL: it contains the bot token.
        raise TelegramError(f"{method}: {type(exc).__name__}") from None

    if data.get("ok"):
        return data.get("result")
    description = str(data.get("description", "unknown error"))
    status = int(data.get("error_code") or response.status_code)
    if status == 403:
        raise BotBlocked(description, status=status)
    retry_after = (data.get("parameters") or {}).get("retry_after")
    raise TelegramError(f"{method}: {description}", status=status, retry_after=retry_after)


def web_app_button(text: str, url: str) -> dict:
    return {"text": text, "web_app": {"url": url}}


def inline_keyboard(*rows: list[dict]) -> dict:
    return {"inline_keyboard": [list(row) for row in rows]}
