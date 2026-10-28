"""Verification of data signed by Telegram for a Mini App.

Telegram signs the Mini App's launch parameters (``initData``) and the contact a user shares through
``requestContact`` the same way: every ``key=value`` pair except ``hash``, sorted by key and joined with
newlines, is HMAC-SHA256'd with a key derived from the bot token.
See https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import DomainError

# Generous enough for any real launch string; refuses junk before any hashing.
MAX_RAW_LENGTH = 4096
# A signature from the future means a skewed clock at best; allow a little drift.
CLOCK_SKEW_SECONDS = 60


class InvalidInitData(DomainError):
    status_code = 401
    default_detail = _("Telegram sign-in data is invalid. Close the app and open it again from the bot.")
    default_code = "telegram_init_data_invalid"


class ExpiredInitData(InvalidInitData):
    default_detail = _("Your Telegram session has expired. Close the app and open it again from the bot.")
    default_code = "telegram_init_data_expired"


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = ""
    is_premium: bool = False
    allows_write_to_pm: bool = False
    photo_url: str = ""

    @classmethod
    def from_payload(cls, payload: dict) -> "TelegramUser":
        try:
            user_id = int(payload["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidInitData() from exc
        return cls(
            id=user_id,
            first_name=str(payload.get("first_name") or "")[:64],
            last_name=str(payload.get("last_name") or "")[:64],
            username=str(payload.get("username") or "")[:32],
            language_code=str(payload.get("language_code") or "")[:10],
            is_premium=bool(payload.get("is_premium")),
            allows_write_to_pm=bool(payload.get("allows_write_to_pm")),
            photo_url=str(payload.get("photo_url") or "")[:500],
        )


@dataclass(frozen=True)
class InitData:
    user: TelegramUser
    auth_date: int
    start_param: str = ""
    chat_type: str = ""
    raw_fields: dict = field(default_factory=dict, repr=False)


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def verify_signed(raw: str, *, bot_token: str | None = None, max_age: int | None = None) -> dict[str, str]:
    """Check the signature and age of a Telegram-signed query string and return its fields."""
    bot_token = bot_token if bot_token is not None else settings.TELEGRAM_BOT_TOKEN
    max_age = max_age if max_age is not None else settings.TELEGRAM_INIT_DATA_MAX_AGE
    if not bot_token or not raw or len(raw) > MAX_RAW_LENGTH:
        raise InvalidInitData()

    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InvalidInitData() from exc
    fields = dict(pairs)
    if len(fields) != len(pairs):  # a repeated key could make two readers disagree
        raise InvalidInitData()

    received_hash = fields.pop("hash", "")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    expected = hmac.new(_secret_key(bot_token), check_string.encode(), hashlib.sha256).hexdigest()
    if not received_hash or not hmac.compare_digest(expected, received_hash):
        raise InvalidInitData()

    try:
        auth_date = int(fields.get("auth_date", ""))
    except ValueError as exc:
        raise InvalidInitData() from exc
    age = time.time() - auth_date
    if age > max_age:
        raise ExpiredInitData()
    if age < -CLOCK_SKEW_SECONDS:
        raise InvalidInitData()
    return fields


def _json_field(fields: dict[str, str], key: str) -> dict:
    try:
        value = json.loads(fields[key])
    except (KeyError, ValueError) as exc:
        raise InvalidInitData() from exc
    if not isinstance(value, dict):
        raise InvalidInitData()
    return value


def parse_init_data(raw: str, **kwargs) -> InitData:
    """Verify the Mini App's ``initData`` and return who opened it."""
    fields = verify_signed(raw, **kwargs)
    return InitData(
        user=TelegramUser.from_payload(_json_field(fields, "user")),
        auth_date=int(fields["auth_date"]),
        start_param=fields.get("start_param", "")[:64],
        chat_type=fields.get("chat_type", ""),
        raw_fields=fields,
    )


@dataclass(frozen=True)
class SharedContact:
    user_id: int
    phone_number: str


def parse_contact(raw: str, **kwargs) -> SharedContact:
    """Verify the signed result of ``WebApp.requestContact`` and return the shared phone number."""
    fields = verify_signed(raw, **kwargs)
    contact = _json_field(fields, "contact")
    try:
        return SharedContact(user_id=int(contact["user_id"]), phone_number=str(contact["phone_number"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidInitData() from exc
