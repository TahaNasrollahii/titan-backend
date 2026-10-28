from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TelegramConfig(AppConfig):
    name = "apps.telegram"
    label = "telegram"
    verbose_name = _("Telegram")

    def ready(self):
        from . import receivers  # noqa: F401  (connects the notification push)
