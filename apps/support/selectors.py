from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery

from apps.orders.models import Order
from apps.payments.models import Wallet

from .models import Conversation, Message, SupportAgent

QUEUE_LIMIT = 200
MESSAGES_LIMIT = 300
CUSTOMER_ORDERS = 3


def public_messages(conversation: Conversation, *, after: int = 0) -> QuerySet[Message]:
    """What the visitor sees: everything except internal notes."""
    return (
        conversation.messages.filter(id__gt=after, kind__in=Message.PUBLIC_KINDS)
        .select_related("author__support_agent")
        .order_by("id")[:MESSAGES_LIMIT]
    )


def desk_messages(conversation: Conversation, *, after: int = 0) -> QuerySet[Message]:
    return (
        conversation.messages.filter(id__gt=after)
        .select_related("author__support_agent")
        .order_by("id")[:MESSAGES_LIMIT]
    )


def online_agents() -> QuerySet[SupportAgent]:
    return SupportAgent.objects.online().select_related("user").order_by("user_id")


def staff_agents() -> list[SupportAgent]:
    """Every active staff member, with a (possibly unsaved) agent profile, for transfers."""
    from apps.accounts.models import User

    users = User.objects.filter(is_staff=True, is_active=True).select_related("support_agent").order_by("id")
    return [getattr(user, "support_agent", None) or SupportAgent(user=user) for user in users]


def conversations() -> QuerySet[Conversation]:
    """Conversations with what the desk list needs: unread count and the last public message."""
    last = Message.objects.filter(conversation=OuterRef("pk"), kind__in=Message.PUBLIC_KINDS).order_by("-id")
    return Conversation.objects.select_related("assignee__support_agent", "user").annotate(
        unread=Count(
            "messages",
            filter=Q(messages__kind=Message.Kind.VISITOR, messages__id__gt=F("agent_read_id")),
        ),
        last_kind=Subquery(last.values("kind")[:1]),
        last_body=Subquery(last.values("body")[:1]),
        last_image=Subquery(last.values("image")[:1]),
    )


def desk_queue() -> list[Conversation]:
    """Open conversations: the longest-waiting first, then the most recently active."""
    return list(
        conversations()
        .filter(status=Conversation.Status.OPEN)
        .order_by(F("awaiting_since").asc(nulls_last=True), F("last_message_at").desc(nulls_last=True))[
            :QUEUE_LIMIT
        ]
    )


def customer(conversation: Conversation) -> dict:
    user = conversation.user
    orders = Order.objects.filter(user=user).order_by("-created_at") if user else Order.objects.none()
    wallet = Wallet.objects.filter(user=user).values_list("balance", flat=True).first() if user else None
    return {
        "user_id": user.pk if user else None,
        "username": user.username if user else None,
        "display_name": user.display_name if user else None,
        "avatar": user.avatar if user else None,
        "date_joined": user.date_joined if user else None,
        "wallet_balance": wallet,
        "orders_count": orders.count(),
        "recent_orders": list(orders[:CUSTOMER_ORDERS]),
        "previous_conversations": Conversation.objects.filter(phone=conversation.phone)
        .exclude(pk=conversation.pk)
        .count(),
    }
