"""Live support chat: visitors talk to staff, who answer from the desk page on the storefront (``/desk``).

A visitor is identified by a random token handed out when the conversation starts; only its SHA-256
hash is stored. Clients poll for changes (``after=<last message id>``), so every read is a cheap
indexed query and the same API can later be pushed over WebSockets without changing its shape.
"""

import hashlib
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.validators import IMAGE_VALIDATORS


def _seconds_ago(seconds: int):
    return timezone.now() - timedelta(seconds=seconds)


class SupportAgentQuerySet(models.QuerySet):
    def online(self):
        """Staff who switched themselves on and have the desk open right now."""
        return self.filter(
            is_available=True,
            last_seen__gte=_seconds_ago(settings.SUPPORT_AGENT_TIMEOUT_SECONDS),
            user__is_active=True,
            user__is_staff=True,
        )


class SupportAgent(models.Model):
    """A staff member's live-chat profile: the name visitors see, availability and SMS alerts."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_agent"
    )
    display_name = models.CharField(
        _("public name"),
        max_length=40,
        blank=True,
        help_text=_("The name visitors see, e.g. Sara. Defaults to the first word of the full name."),
    )
    is_available = models.BooleanField(_("available"), default=False)
    sms_alerts = models.BooleanField(
        _("SMS alerts"),
        default=True,
        help_text=_("Text this agent when a visitor writes and nobody is online."),
    )
    last_seen = models.DateTimeField(null=True, blank=True)
    last_alerted_at = models.DateTimeField(null=True, blank=True)

    objects = SupportAgentQuerySet.as_manager()

    class Meta:
        verbose_name = _("Support agent")
        verbose_name_plural = _("Support agents")

    def __str__(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        if self.display_name:
            return self.display_name
        first_word = (self.user.full_name or "").split()
        return first_word[0] if first_word else (self.user.username or str(_("Support")))

    @property
    def is_online(self) -> bool:
        return bool(
            self.is_available
            and self.last_seen
            and self.last_seen >= _seconds_ago(settings.SUPPORT_AGENT_TIMEOUT_SECONDS)
        )


class Conversation(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        CLOSED = "closed", _("Closed")

    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    first_name = models.CharField(_("first name"), max_length=40)
    last_name = models.CharField(_("last name"), max_length=60)
    phone = models.CharField(_("mobile number"), max_length=11, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_conversations",
        help_text=_("Set when the visitor was logged in."),
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_conversations",
    )
    page_url = models.CharField(_("started on"), max_length=500, blank=True)

    last_message_at = models.DateTimeField(null=True, blank=True)
    awaiting_since = models.DateTimeField(
        null=True, blank=True, help_text=_("Oldest visitor message that has not been answered yet.")
    )
    visitor_last_seen = models.DateTimeField(null=True, blank=True)
    visitor_typing_at = models.DateTimeField(null=True, blank=True)
    agent_typing_at = models.DateTimeField(null=True, blank=True)
    visitor_read_id = models.PositiveBigIntegerField(
        default=0, help_text=_("Last message the visitor has seen.")
    )
    agent_read_id = models.PositiveBigIntegerField(default=0, help_text=_("Last message support has seen."))

    rating = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    rating_comment = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    alerted_at = models.DateTimeField(null=True, blank=True, help_text=_("When agents were texted about it."))
    visitor_notified_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When the visitor was last texted about a reply.")
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-last_message_at"])]
        verbose_name = _("Conversation")
        verbose_name_plural = _("Conversations")

    def __str__(self) -> str:
        return f"{self.ticket} · {self.full_name}"

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @property
    def ticket(self) -> str:
        return f"T-{(self.pk or 0) + 1000}"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN

    @property
    def visitor_online(self) -> bool:
        return bool(
            self.visitor_last_seen
            and self.visitor_last_seen >= _seconds_ago(settings.SUPPORT_VISITOR_TIMEOUT_SECONDS)
        )

    @property
    def visitor_typing(self) -> bool:
        return self._typing(self.visitor_typing_at)

    @property
    def agent_typing(self) -> bool:
        return self._typing(self.agent_typing_at)

    def _typing(self, at) -> bool:
        return bool(self.is_open and at and at >= _seconds_ago(settings.SUPPORT_TYPING_SECONDS))


def attachment_path(instance: "Message", filename: str) -> str:
    """Random file names: attachments are served from public storage, so URLs must not be guessable."""
    return f"support/{instance.conversation_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class Message(models.Model):
    class Kind(models.TextChoices):
        VISITOR = "visitor", _("Visitor")
        AGENT = "agent", _("Agent")
        NOTE = "note", _("Internal note")
        SYSTEM = "system", _("System")

    PUBLIC_KINDS = (Kind.VISITOR, Kind.AGENT, Kind.SYSTEM)

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    body = models.TextField(blank=True)
    image = models.ImageField(upload_to=attachment_path, blank=True, validators=IMAGE_VALIDATORS)
    client_id = models.UUIDField(
        null=True, blank=True, help_text=_("Sent by the client so a retried request is not stored twice.")
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "client_id"],
                condition=models.Q(client_id__isnull=False),
                name="support_message_client_id_unique",
            )
        ]
        verbose_name = _("Message")
        verbose_name_plural = _("Messages")

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.body[:40] or '[image]'}"


class CannedReply(models.Model):
    """A saved answer agents insert by typing ``/`` and its shortcut in the desk composer."""

    shortcut = models.SlugField(_("shortcut"), max_length=30, unique=True, allow_unicode=True)
    title = models.CharField(_("title"), max_length=80)
    body = models.TextField(_("text"), max_length=2000)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "title"]
        verbose_name = _("Canned reply")
        verbose_name_plural = _("Canned replies")

    def __str__(self) -> str:
        return f"/{self.shortcut}"
