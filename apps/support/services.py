import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Min, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import override

from apps.accounts.models import User
from apps.accounts.sms import get_sms_backend
from apps.core.exceptions import ConflictError, DomainError, ExternalServiceError
from apps.notifications.models import Notification
from apps.notifications.services import notify

from .models import Conversation, Message, SupportAgent

logger = logging.getLogger(__name__)

Kind = Message.Kind
RESPONSE_STATS_CACHE_KEY = "support:response-seconds"
RESPONSE_STATS_DAYS = 7
RESPONSE_STATS_SAMPLE = 200
# Presence timestamps are written at most this often, so polling does not turn into a write per request.
TOUCH_EVERY_SECONDS = 10


# ------------------------------------------------------------------ errors
class ConversationNotFound(DomainError):
    status_code = 404
    default_detail = _("This conversation was not found. Start a new one.")
    default_code = "conversation_not_found"


class ConversationClosed(ConflictError):
    default_detail = _("This conversation has ended.")
    default_code = "conversation_closed"


class ConversationStillOpen(ConflictError):
    default_detail = _("End the conversation before rating it.")
    default_code = "conversation_open"


class AlreadyRated(ConflictError):
    default_detail = _("You have already rated this conversation.")
    default_code = "already_rated"


class AlreadyAssigned(ConflictError):
    default_detail = _("Another agent has already taken this conversation.")
    default_code = "already_assigned"


# ------------------------------------------------------------------ visitors
def start_conversation(
    *, first_name: str, last_name: str, phone: str, user: User | None = None, page_url: str = ""
) -> tuple[Conversation, str]:
    """Open a conversation and return it with the visitor's secret token (shown once, stored hashed)."""
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    conversation = Conversation.objects.create(
        token_hash=Conversation.hash_token(token),
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        user=user if user and user.is_authenticated else None,
        page_url=page_url[:500],
        last_message_at=now,
        visitor_last_seen=now,
    )
    return conversation, token


def conversation_for_token(token: str | None) -> Conversation:
    if not token:
        raise ConversationNotFound()
    conversation = (
        Conversation.objects.select_related("assignee__support_agent")
        .filter(token_hash=Conversation.hash_token(token))
        .first()
    )
    if conversation is None:
        raise ConversationNotFound()
    return conversation


def touch_visitor(conversation: Conversation) -> None:
    now = timezone.now()
    stale = now - timedelta(seconds=TOUCH_EVERY_SECONDS)
    Conversation.objects.filter(pk=conversation.pk).filter(
        Q(visitor_last_seen__isnull=True) | Q(visitor_last_seen__lt=stale)
    ).update(visitor_last_seen=now)


def rate(conversation: Conversation, *, rating: int, comment: str = "") -> Conversation:
    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
        if conversation.is_open:
            raise ConversationStillOpen()
        if conversation.rating is not None:
            raise AlreadyRated()
        conversation.rating = rating
        conversation.rating_comment = comment.strip()
        conversation.save(update_fields=["rating", "rating_comment", "updated_at"])
    return conversation


# ------------------------------------------------------------------ messages (both sides)
def post_message(
    conversation: Conversation,
    *,
    kind: str,
    author: User | None = None,
    body: str = "",
    image=None,
    client_id=None,
) -> tuple[Message, bool]:
    """Store a message. Returns ``(message, created)``; a repeated ``client_id`` returns the original."""
    if client_id:
        existing = conversation.messages.filter(client_id=client_id).first()
        if existing:
            return existing, False

    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
        if not conversation.is_open and kind != Kind.NOTE:
            raise ConversationClosed()
        # An agent's first reply to a waiting conversation claims it, announced before the reply.
        if kind == Kind.AGENT and conversation.assignee_id is None:
            _set_assignee(conversation, author, by=author)
        try:
            with transaction.atomic():
                message = Message.objects.create(
                    conversation=conversation,
                    kind=kind,
                    author=author,
                    body=body.strip(),
                    image=image or "",
                    client_id=client_id,
                )
        except IntegrityError:
            # Two identical retries raced; the other one won.
            return conversation.messages.get(client_id=client_id), False

        fields = ["last_message_at", "updated_at"]
        conversation.last_message_at = message.created_at
        if kind == Kind.VISITOR:
            if conversation.awaiting_since is None:
                conversation.awaiting_since = message.created_at
            conversation.visitor_typing_at = None
            conversation.visitor_read_id = message.pk
            fields += ["awaiting_since", "visitor_typing_at", "visitor_read_id"]
        else:
            if kind == Kind.AGENT:
                conversation.awaiting_since = None
                conversation.agent_typing_at = None
                fields += ["awaiting_since", "agent_typing_at"]
            conversation.agent_read_id = max(conversation.agent_read_id, message.pk)
            fields.append("agent_read_id")
        conversation.save(update_fields=fields)

    if kind == Kind.VISITOR:
        alert_agents_if_nobody_online(conversation)
    elif kind == Kind.AGENT:
        notify_visitor_if_away(conversation)
    return message, True


def mark_read(conversation: Conversation, *, side: str, last_id: int) -> None:
    """Move a read marker forward only, never past the newest message."""
    field = "visitor_read_id" if side == Kind.VISITOR else "agent_read_id"
    newest = conversation.messages.order_by("-id").values_list("id", flat=True).first() or 0
    last_id = min(last_id, newest)
    Conversation.objects.filter(pk=conversation.pk, **{f"{field}__lt": last_id}).update(**{field: last_id})


def set_typing(conversation: Conversation, *, side: str) -> None:
    field = "visitor_typing_at" if side == Kind.VISITOR else "agent_typing_at"
    Conversation.objects.filter(pk=conversation.pk, status=Conversation.Status.OPEN).update(
        **{field: timezone.now()}
    )


def close(conversation: Conversation, *, by: User | None = None) -> Conversation:
    """End a conversation. ``by`` is the agent who closed it; ``None`` means the visitor did."""
    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
        if not conversation.is_open:
            return conversation
        text = _("Support ended the conversation.") if by else _("The visitor ended the conversation.")
        _system(conversation, text)
        conversation.status = Conversation.Status.CLOSED
        conversation.closed_at = timezone.now()
        conversation.awaiting_since = None
        conversation.visitor_typing_at = None
        conversation.agent_typing_at = None
        conversation.save(
            update_fields=[
                "status",
                "closed_at",
                "awaiting_since",
                "visitor_typing_at",
                "agent_typing_at",
                "last_message_at",
                "updated_at",
            ]
        )
    return conversation


# ------------------------------------------------------------------ agents
def agent_profile(user: User) -> SupportAgent:
    agent, _created = SupportAgent.objects.select_related("user").get_or_create(user=user)
    return agent


def touch_agent(agent: SupportAgent) -> None:
    now = timezone.now()
    if agent.last_seen and agent.last_seen > now - timedelta(seconds=TOUCH_EVERY_SECONDS):
        return
    SupportAgent.objects.filter(pk=agent.pk).update(last_seen=now)
    agent.last_seen = now


def assign(conversation: Conversation, agent: User | None, *, by: User) -> Conversation:
    """Claim (agent is ``by``), transfer to a colleague, or return to the queue (``agent=None``)."""
    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
        if not conversation.is_open:
            raise ConversationClosed()
        if agent is not None and agent.pk == conversation.assignee_id:
            return conversation
        if agent is not None and agent.pk == by.pk and conversation.assignee_id not in (None, by.pk):
            # "Claim" lost a race with a colleague; transferring is a separate, explicit action.
            raise AlreadyAssigned()
        _set_assignee(conversation, agent, by=by)
    return conversation


def _set_assignee(conversation: Conversation, agent: User | None, *, by: User | None) -> None:
    """Change the assignee inside the caller's transaction and record it in the conversation."""
    previous = conversation.assignee_id
    conversation.assignee = agent
    conversation.save(update_fields=["assignee", "updated_at"])
    if agent is None:
        _system(
            conversation, _("Returned to the queue by %(name)s."), {"name": _agent_name(by)}, kind=Kind.NOTE
        )
    elif previous is None and (by is None or by.pk == agent.pk):
        _system(conversation, _("%(name)s joined the conversation"), {"name": _agent_name(agent)})
    else:
        _system(conversation, _("Conversation transferred to %(name)s"), {"name": _agent_name(agent)})


def _agent_name(user: User | None):
    return agent_profile(user).name if user else _("Support")


def _system(
    conversation: Conversation, text, params: dict | None = None, *, kind: str = Kind.SYSTEM
) -> Message:
    """Record an event in the conversation. Stored in Persian, the language visitors read."""
    with override("fa"):
        body = str(text) % {key: str(value) for key, value in params.items()} if params else str(text)
    message = Message.objects.create(conversation=conversation, kind=kind, body=body)
    conversation.last_message_at = message.created_at
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=message.created_at)
    return message


# ------------------------------------------------------------------ SMS alerts
def alert_agents_if_nobody_online(conversation: Conversation) -> int:
    """Text opted-in agents once per conversation when a visitor writes and nobody is online."""
    if conversation.alerted_at or SupportAgent.objects.online().exists():
        return 0
    now = timezone.now()
    # Claim the alert atomically so two quick messages never send it twice.
    if not Conversation.objects.filter(pk=conversation.pk, alerted_at__isnull=True).update(alerted_at=now):
        return 0
    conversation.alerted_at = now
    cooldown = now - timedelta(seconds=settings.SUPPORT_ALERT_COOLDOWN_SECONDS)
    agents = SupportAgent.objects.filter(sms_alerts=True, user__is_active=True, user__is_staff=True).filter(
        Q(last_alerted_at__isnull=True) | Q(last_alerted_at__lt=cooldown)
    )
    sent = 0
    for agent in agents.select_related("user"):
        try:
            get_sms_backend().send_support_alert(agent.user.phone, conversation.ticket)
        except ExternalServiceError:
            logger.warning("Support alert to agent %s failed", agent.pk)
            continue
        SupportAgent.objects.filter(pk=agent.pk).update(last_alerted_at=now)
        sent += 1
    return sent


def notify_visitor_if_away(conversation: Conversation) -> bool:
    """Text the visitor that support answered, if they have left the page (at most once per cooldown)."""
    conversation.refresh_from_db(fields=["visitor_last_seen", "visitor_notified_at"])
    if conversation.visitor_online:
        return False
    now = timezone.now()
    cooldown = now - timedelta(seconds=settings.SUPPORT_ALERT_COOLDOWN_SECONDS)
    claimed = (
        Conversation.objects.filter(pk=conversation.pk)
        .filter(Q(visitor_notified_at__isnull=True) | Q(visitor_notified_at__lt=cooldown))
        .update(visitor_notified_at=now)
    )
    if not claimed:
        return False
    if conversation.user_id:
        # In-app too: the Telegram bot mirrors notifications, so Mini App users hear about it there.
        notify(
            conversation.user,
            kind=Notification.Kind.SYSTEM,
            title=_("Support answered your message"),
            body=_("Ticket %(ticket)s has a new reply.") % {"ticket": conversation.ticket},
            data={"support_ticket": conversation.ticket},
        )
    try:
        get_sms_backend().send_support_reply(conversation.phone, conversation.ticket)
    except ExternalServiceError:
        logger.warning("Support reply SMS for conversation %s failed", conversation.pk)
        return False
    return True


# ------------------------------------------------------------------ public status
def average_response_seconds() -> int | None:
    """Mean time to the first agent reply over the last week (cached for five minutes)."""
    try:
        cached = cache.get(RESPONSE_STATS_CACHE_KEY)
        if cached is not None:
            return cached or None
        since = timezone.now() - timedelta(days=RESPONSE_STATS_DAYS)
        rows = (
            Conversation.objects.filter(created_at__gte=since)
            .annotate(
                first_visitor=Min("messages__created_at", filter=Q(messages__kind=Kind.VISITOR)),
                first_agent=Min("messages__created_at", filter=Q(messages__kind=Kind.AGENT)),
            )
            .filter(first_visitor__isnull=False, first_agent__isnull=False)
            .order_by("-created_at")
            .values_list("first_visitor", "first_agent")[:RESPONSE_STATS_SAMPLE]
        )
        waits = []
        for visitor, agent in rows:
            if isinstance(visitor, str):
                from django.utils.dateparse import parse_datetime

                visitor = parse_datetime(visitor)
            if isinstance(agent, str):
                from django.utils.dateparse import parse_datetime

                agent = parse_datetime(agent)
            if visitor and agent and agent >= visitor:
                waits.append((agent - visitor).total_seconds())
        seconds = round(sum(waits) / len(waits)) if waits else 0
        cache.set(RESPONSE_STATS_CACHE_KEY, seconds, 300)
        return seconds or None
    except Exception as e:
        logger.warning("Failed to calculate average_response_seconds: %s", e)
        return None
