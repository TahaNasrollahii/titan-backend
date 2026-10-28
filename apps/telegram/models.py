from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class TelegramAccount(TimeStampedModel):
    """A Telegram user linked to a Titan account through the Mini App.

    The link is made once, when the user shares their (Telegram-verified) phone number; afterwards the
    signed launch data alone signs them in.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_account"
    )
    telegram_id = models.BigIntegerField(_("Telegram user id"), unique=True)
    username = models.CharField(max_length=32, blank=True)
    first_name = models.CharField(max_length=64, blank=True)
    last_name = models.CharField(max_length=64, blank=True)
    language_code = models.CharField(max_length=10, blank=True)
    photo_url = models.URLField(max_length=500, blank=True)
    is_premium = models.BooleanField(default=False)

    notifications_enabled = models.BooleanField(
        _("bot notifications"), default=True, help_text=_("Send the user's notifications as bot messages.")
    )
    can_message = models.BooleanField(
        _("bot can message"),
        default=False,
        help_text=_("The user has started the bot or allowed it to write; cleared when they block it."),
    )
    last_opened_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Telegram account")
        verbose_name_plural = _("Telegram accounts")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"@{self.username}" if self.username else str(self.telegram_id)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()
