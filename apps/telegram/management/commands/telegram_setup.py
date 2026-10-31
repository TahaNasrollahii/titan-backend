"""Point the bot at this deployment: webhook, menu button, commands and profile texts.

    python manage.py telegram_setup --webhook-url https://<backend-host>/api/v1/telegram/webhook/

Idempotent; run it again whenever the backend or Mini App URL changes.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.telegram import bot

DESCRIPTION = (
    "تایتان؛ خرید آنی آیتم و ارز بازی، شرکت در تورنمنت‌ها، مدیریت تیم و کیف پول، همه در یک اپ.\n"
    "برای شروع روی «باز کردن تایتان» بزنید."
)
SHORT_DESCRIPTION = "فروشگاه بازی و تورنمنت‌های تایتان، داخل تلگرام 🎮"


class Command(BaseCommand):
    help = "Configure the Telegram bot (webhook, menu button, commands, descriptions)."

    def add_arguments(self, parser):
        parser.add_argument("--webhook-url", required=True, help="Public HTTPS URL of telegram/webhook/.")
        parser.add_argument("--drop-pending", action="store_true", help="Discard updates queued meanwhile.")

    def handle(self, *args, webhook_url: str, drop_pending: bool, **options):
        if not bot.is_configured():
            raise CommandError("Set TELEGRAM_BOT_TOKEN first.")
        if not settings.TELEGRAM_WEBHOOK_SECRET or len(settings.TELEGRAM_WEBHOOK_SECRET) < 32:
            raise CommandError(
                "Set TELEGRAM_WEBHOOK_SECRET to a random string of 32+ characters ([A-Za-z0-9_-])."
            )
        if not settings.TELEGRAM_MINIAPP_URL.startswith("https://"):
            raise CommandError("Set TELEGRAM_MINIAPP_URL to the Mini App's https:// address.")
        if not webhook_url.startswith("https://"):
            raise CommandError("The webhook URL must be https://.")

        steps = [
            (
                "setWebhook",
                {
                    "url": webhook_url,
                    "secret_token": settings.TELEGRAM_WEBHOOK_SECRET,
                    "allowed_updates": ["message", "my_chat_member"],
                    "drop_pending_updates": drop_pending,
                    "max_connections": 40,
                },
            ),
            (
                "setChatMenuButton",
                {
                    "menu_button": {
                        "type": "web_app",
                        "text": "تایتان",
                        "web_app": {"url": settings.TELEGRAM_MINIAPP_URL},
                    }
                },
            ),
            (
                "setMyCommands",
                {
                    "commands": [
                        {"command": "start", "description": "باز کردن تایتان"},
                        {"command": "guides", "description": "آموزش‌ها، راهنمای خرید و رنک‌بندی"},
                    ]
                },
            ),
            ("setMyDescription", {"description": DESCRIPTION}),
            ("setMyShortDescription", {"short_description": SHORT_DESCRIPTION}),
        ]
        for method, params in steps:
            try:
                bot.call(method, **params)
            except bot.TelegramError as exc:
                raise CommandError(f"{method} failed: {exc}") from exc
            self.stdout.write(self.style.SUCCESS(f"✓ {method}"))

        username = bot.call("getMe")["username"]
        self.stdout.write(
            f"\nBot @{username} is ready. In @BotFather → /mybots → Bot Settings → Configure Mini App,\n"
            f"enable the Main Mini App with URL {settings.TELEGRAM_MINIAPP_URL} so it opens\n"
            f"from the bot profile and from links like https://t.me/{username}?startapp=…"
        )
