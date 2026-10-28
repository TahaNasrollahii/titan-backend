"""Telegram Mini App sign-in, account linking, the bot webhook and notification delivery."""

import html
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rest_framework.exceptions import ValidationError

from apps.accounts import services as accounts
from apps.accounts.models import OTPCode, User
from apps.core.exceptions import ConflictError, DomainError, NotAllowedError
from apps.core.utils import normalize_phone
from apps.notifications.models import Notification

from . import bot
from .initdata import InitData, InvalidInitData, TelegramUser, parse_contact
from .models import TelegramAccount

logger = logging.getLogger(__name__)

# A contact the user shared reaches the bot as a message too; it waits here for the Mini App to claim it.
CONTACT_CACHE_KEY = "telegram:contact:{}"
CONTACT_TTL_SECONDS = 10 * 60
PUSH_WORKERS = 8


# ------------------------------------------------------------------ errors
class ContactNotReceived(ConflictError):
    default_detail = _("Your phone number has not reached us yet. Try again in a moment.")
    default_code = "telegram_contact_pending"


class ContactMismatch(DomainError):
    status_code = 403
    default_detail = _("Share your own phone number to continue.")
    default_code = "telegram_contact_mismatch"


class PhoneNotSupported(DomainError):
    default_detail = _(
        "Your Telegram number is not an Iranian mobile number. Sign in with your Titan mobile number instead."
    )
    default_code = "telegram_phone_not_supported"


class AccountDisabled(NotAllowedError):
    default_detail = _("This account has been disabled.")
    default_code = "account_disabled"


# ------------------------------------------------------------------ sign-in & linking
def sign_in(init_data: InitData) -> TelegramAccount | None:
    """Return the Titan account linked to whoever opened the Mini App, or None if it is not linked yet."""
    account = TelegramAccount.objects.select_related("user").filter(telegram_id=init_data.user.id).first()
    if account is None:
        return None
    if not account.user.is_active:
        raise AccountDisabled()
    _refresh_profile(account, init_data.user)
    return account


def _profile_fields(tg: TelegramUser) -> dict:
    return {
        "username": tg.username,
        "first_name": tg.first_name,
        "last_name": tg.last_name,
        "language_code": tg.language_code,
        "photo_url": tg.photo_url if tg.photo_url.startswith("https://") else "",
        "is_premium": tg.is_premium,
    }


def _refresh_profile(account: TelegramAccount, tg: TelegramUser) -> None:
    for key, value in _profile_fields(tg).items():
        setattr(account, key, value)
    account.last_opened_at = timezone.now()
    # Only ever upgrade here: a launch from an inline button does not carry the flag.
    account.can_message = account.can_message or tg.allows_write_to_pm
    account.save()


def remember_contact(telegram_id: int, phone_number: str) -> None:
    cache.set(CONTACT_CACHE_KEY.format(telegram_id), phone_number, CONTACT_TTL_SECONDS)


def link_with_contact(init_data: InitData, contact_raw: str = "") -> tuple[TelegramAccount, bool]:
    """Link via the phone number the user shared with ``requestContact``.

    The client passes the signed contact Telegram returned. If that is missing or cannot be verified, the
    copy Telegram delivered to the bot webhook is used instead; both are vouched for by Telegram.
    """
    tg = init_data.user
    phone_number = ""
    if contact_raw:
        try:
            contact = parse_contact(contact_raw)
        except InvalidInitData:
            logger.info("Unverifiable shared contact for Telegram user %s; using the webhook copy", tg.id)
        else:
            if contact.user_id != tg.id:
                raise ContactMismatch()
            phone_number = contact.phone_number
    phone_number = phone_number or cache.get(CONTACT_CACHE_KEY.format(tg.id), "")
    if not phone_number:
        raise ContactNotReceived()
    try:
        phone = normalize_phone(phone_number)
    except ValidationError as exc:
        raise PhoneNotSupported() from exc
    result = _link(tg, phone)
    cache.delete(CONTACT_CACHE_KEY.format(tg.id))
    return result


def request_link_code(phone: str) -> None:
    """SMS fallback for users whose Telegram number is not their (Iranian) Titan number."""
    accounts.request_otp(phone, OTPCode.Purpose.LOGIN)


def link_with_code(init_data: InitData, phone: str, code: str) -> tuple[TelegramAccount, bool]:
    accounts.consume_otp(phone, code, OTPCode.Purpose.LOGIN)
    return _link(init_data.user, phone)


@transaction.atomic
def _link(tg: TelegramUser, phone: str) -> tuple[TelegramAccount, bool]:
    user, created = User.objects.select_for_update().get_or_create(phone=phone)
    if not user.is_active:
        raise AccountDisabled()
    if created:
        user.set_unusable_password()
        user.full_name = f"{tg.first_name} {tg.last_name}".strip()[:120]
        user.save(update_fields=["password", "full_name"])
    # Whoever proved the phone number owns the account: an older link on either side is replaced.
    TelegramAccount.objects.filter(Q(user=user) | Q(telegram_id=tg.id)).delete()
    account = TelegramAccount.objects.create(
        user=user,
        telegram_id=tg.id,
        can_message=tg.allows_write_to_pm,
        last_opened_at=timezone.now(),
        **_profile_fields(tg),
    )
    return account, created


def unlink(user: User) -> None:
    TelegramAccount.objects.filter(user=user).delete()


def update_settings(account: TelegramAccount, *, notifications_enabled=None, write_access_granted=None):
    if notifications_enabled is not None:
        account.notifications_enabled = notifications_enabled
    if write_access_granted:
        account.can_message = True
    account.save(update_fields=["notifications_enabled", "can_message", "updated_at"])
    return account


# ------------------------------------------------------------------ links into the Mini App
def app_url(path: str = "/") -> str:
    return f"{settings.TELEGRAM_MINIAPP_URL}{path}"


def notification_path(notification: Notification) -> str:
    """The Mini App screen a notification's button opens (mirrors the app's routes)."""
    data = notification.data or {}
    if data.get("order_number"):
        return f"/orders/{quote(str(data['order_number']), safe='')}"
    if data.get("tournament_slug"):
        return f"/tournaments/{quote(str(data['tournament_slug']), safe='')}"
    if data.get("invitation_id"):
        return "/invitations"
    if data.get("team_id"):
        return f"/teams/{int(data['team_id'])}"
    if data.get("friendship_id"):
        return "/friends"
    if data.get("support_ticket"):
        return "/support"
    if notification.kind == Notification.Kind.PAYMENT:
        return "/wallet"
    return "/notifications"


# ------------------------------------------------------------------ webhook
WELCOME_TEXT = _(
    "<b>Welcome to Titan</b> 🎮\n\n"
    "Game top-ups, tournaments, your team and your wallet, all in one app.\n"
    "Tap the button below to open Titan."
)
NUDGE_TEXT = _("Everything happens inside the Titan app. Tap the button below to open it.")
OPEN_APP = _("Open Titan")


def _send_message_reply(chat_id: int, text: str, path: str = "/") -> dict:
    """A Bot API call returned as the webhook response, saving a round trip to Telegram."""
    return {
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": str(text),
        "parse_mode": "HTML",
        "reply_markup": bot.inline_keyboard([bot.web_app_button(str(OPEN_APP), app_url(path))]),
    }


def handle_update(update: dict) -> dict | None:
    """Process one webhook update; may return a Bot API call to make in the response."""
    if member := update.get("my_chat_member"):
        _chat_member_changed(member)
        return None

    message = update.get("message")
    if not isinstance(message, dict) or (message.get("chat") or {}).get("type") != "private":
        return None
    sender_id = (message.get("from") or {}).get("id")
    if not isinstance(sender_id, int):
        return None

    if contact := message.get("contact"):
        # Only the user's own number: a forwarded contact card belongs to somebody else.
        if contact.get("user_id") == sender_id and contact.get("phone_number"):
            remember_contact(sender_id, str(contact["phone_number"]))
        return None

    # Writing to the bot allows it to write back.
    TelegramAccount.objects.filter(telegram_id=sender_id, can_message=False).update(can_message=True)
    if not settings.TELEGRAM_MINIAPP_URL:
        return None
    text = str(message.get("text") or "")
    return _send_message_reply(sender_id, WELCOME_TEXT if text.startswith("/start") else NUDGE_TEXT)


def _chat_member_changed(member: dict) -> None:
    if (member.get("chat") or {}).get("type") != "private":
        return
    user_id = (member.get("from") or {}).get("id")
    status = (member.get("new_chat_member") or {}).get("status")
    if isinstance(user_id, int) and status in ("kicked", "member"):
        TelegramAccount.objects.filter(telegram_id=user_id).update(can_message=status == "member")


# ------------------------------------------------------------------ notification delivery
def _notification_message(notification: Notification) -> dict:
    text = f"<b>{html.escape(notification.title)}</b>"
    if notification.body:
        text += f"\n\n{html.escape(notification.body)}"
    return {
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
        "reply_markup": bot.inline_keyboard(
            [bot.web_app_button(str(_("View in Titan")), app_url(notification_path(notification)))]
        ),
    }


def _send(job: tuple[int, dict]) -> bool | None:
    """Send one message. True: sent, False: the user is unreachable, None: a transient failure."""
    chat_id, message = job
    try:
        bot.call("sendMessage", chat_id=chat_id, **message)
    except bot.BotBlocked:
        return False
    except bot.TelegramError as exc:
        logger.warning("Telegram push to %s failed: %s", chat_id, exc)
        return None
    return True


def push_notifications(notifications: list[Notification]) -> int:
    """Mirror new notifications into the bot chat of users who opted in. Returns how many were sent.

    Runs after the creating transaction commits. Sends in parallel with a short timeout each so a
    tournament-wide announcement does not hold the request for long; threads only do HTTP, never SQL.
    """
    if not (bot.is_configured() and settings.TELEGRAM_MINIAPP_URL and notifications):
        return 0
    accounts_by_user = {
        account.user_id: account
        for account in TelegramAccount.objects.filter(
            user_id__in={n.user_id for n in notifications}, notifications_enabled=True, can_message=True
        )
    }
    targets = [(accounts_by_user[n.user_id], n) for n in notifications if n.user_id in accounts_by_user]
    if not targets:
        return 0
    jobs = [(account.telegram_id, _notification_message(n)) for account, n in targets]
    with ThreadPoolExecutor(max_workers=min(PUSH_WORKERS, len(jobs))) as pool:
        results = list(pool.map(_send, jobs))
    unreachable = {account.pk for (account, _n), ok in zip(targets, results, strict=True) if ok is False}
    if unreachable:
        TelegramAccount.objects.filter(pk__in=unreachable).update(can_message=False)
    return sum(1 for ok in results if ok)
