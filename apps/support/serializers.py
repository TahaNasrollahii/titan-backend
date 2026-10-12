import re

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import User
from apps.core.serializers import ImageUrlField
from apps.core.utils import normalize_phone
from apps.core.validators import IMAGE_VALIDATORS

from .models import CannedReply, Conversation, Message, SupportAgent

AUTHORED_KINDS = (Message.Kind.AGENT, Message.Kind.NOTE)

# Letters in any script, spaces, ZWNJ (common in Persian names), apostrophes and hyphens.
_NAME = re.compile(r"^[^\W\d_](?:[^\W\d_]|[\s‌'\-])*$")


def _clean_name(value: str) -> str:
    value = " ".join(value.split())
    if len(value) < 2 or not _NAME.match(value):
        raise serializers.ValidationError(_("Enter a valid name."), code="invalid_name")
    return value


# ------------------------------------------------------------------ shared output
class AgentSerializer(serializers.Serializer):
    """How an agent appears to visitors and colleagues."""

    id = serializers.IntegerField(source="user_id")
    name = serializers.CharField()
    avatar = ImageUrlField(source="user.avatar")
    avatar_seed = serializers.IntegerField(source="user.avatar_seed")
    is_online = serializers.BooleanField()


def agent_data(user: User | None, context: dict) -> dict | None:
    if user is None:
        return None
    agent = getattr(user, "support_agent", None) or SupportAgent(user=user)
    return AgentSerializer(agent, context=context).data


class MessageSerializer(serializers.ModelSerializer):
    image = ImageUrlField()
    author = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ["id", "kind", "body", "image", "author", "client_id", "created_at"]
        read_only_fields = fields

    @extend_schema_field(AgentSerializer(allow_null=True))
    def get_author(self, message: Message):
        return agent_data(message.author, self.context) if message.kind in AUTHORED_KINDS else None


class MessageInputSerializer(serializers.Serializer):
    body = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=True, max_length=settings.SUPPORT_MESSAGE_MAX_LENGTH
    )
    image = serializers.ImageField(required=False, allow_null=True, validators=IMAGE_VALIDATORS)
    client_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs.get("body") and not attrs.get("image"):
            raise serializers.ValidationError(
                {"body": _("Write a message or attach an image.")}, code="empty_message"
            )
        return attrs


class ReadInputSerializer(serializers.Serializer):
    last_id = serializers.IntegerField(min_value=0)


# ------------------------------------------------------------------ visitor
class PublicStatusSerializer(serializers.Serializer):
    online = serializers.BooleanField()
    agents = AgentSerializer(many=True)
    avg_response_seconds = serializers.IntegerField(allow_null=True)


class StartInputSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=40)
    last_name = serializers.CharField(max_length=60)
    phone = serializers.CharField(max_length=20)
    page_url = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate_first_name(self, value):
        return _clean_name(value)

    def validate_last_name(self, value):
        return _clean_name(value)

    def validate_phone(self, value):
        return normalize_phone(value)


class VisitorConversationSerializer(serializers.ModelSerializer):
    ticket = serializers.CharField(read_only=True)
    agent = serializers.SerializerMethodField()
    agent_typing = serializers.BooleanField(read_only=True)

    class Meta:
        model = Conversation
        fields = [
            "ticket",
            "status",
            "first_name",
            "last_name",
            "phone",
            "agent",
            "agent_typing",
            "agent_read_id",
            "visitor_read_id",
            "rating",
            "created_at",
            "closed_at",
        ]
        read_only_fields = fields

    @extend_schema_field(AgentSerializer(allow_null=True))
    def get_agent(self, conversation: Conversation):
        return agent_data(conversation.assignee, self.context)


class VisitorSyncSerializer(serializers.Serializer):
    conversation = VisitorConversationSerializer()
    messages = MessageSerializer(many=True)
    support = PublicStatusSerializer()


class StartResponseSerializer(VisitorSyncSerializer):
    token = serializers.CharField()


class RateInputSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True, max_length=500)


# ------------------------------------------------------------------ desk
class DeskMessageInputSerializer(MessageInputSerializer):
    internal = serializers.BooleanField(default=False, help_text="An internal note the visitor never sees.")


class AssignInputSerializer(serializers.Serializer):
    agent = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_staff=True, is_active=True), allow_null=True
    )


class DeskMeSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="user_id", read_only=True)
    name = serializers.CharField(read_only=True)
    avatar = ImageUrlField(source="user.avatar")
    avatar_seed = serializers.IntegerField(source="user.avatar_seed", read_only=True)

    class Meta:
        model = SupportAgent
        fields = ["id", "name", "display_name", "avatar", "avatar_seed", "is_available", "sms_alerts"]


class QueueItemSerializer(serializers.ModelSerializer):
    ticket = serializers.CharField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    assignee = serializers.SerializerMethodField()
    unread = serializers.IntegerField(read_only=True, default=0)
    last_kind = serializers.CharField(read_only=True, allow_null=True, default=None)
    last_body = serializers.CharField(read_only=True, allow_null=True, default=None)
    last_has_image = serializers.SerializerMethodField()
    visitor_online = serializers.BooleanField(read_only=True)
    visitor_typing = serializers.BooleanField(read_only=True)
    username = serializers.CharField(source="user.username", read_only=True, default=None)

    class Meta:
        model = Conversation
        fields = [
            "id",
            "ticket",
            "status",
            "full_name",
            "phone",
            "username",
            "assignee",
            "unread",
            "last_kind",
            "last_body",
            "last_has_image",
            "last_message_at",
            "awaiting_since",
            "visitor_online",
            "visitor_typing",
            "rating",
            "created_at",
            "closed_at",
        ]
        read_only_fields = fields

    @extend_schema_field(AgentSerializer(allow_null=True))
    def get_assignee(self, conversation: Conversation):
        return agent_data(conversation.assignee, self.context)

    def get_last_has_image(self, conversation: Conversation) -> bool:
        return bool(getattr(conversation, "last_image", None))


class CustomerOrderSerializer(serializers.Serializer):
    number = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField(source="get_status_display")
    total = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class CustomerSerializer(serializers.Serializer):
    """What the desk shows next to a conversation: account, recent orders and earlier chats."""

    user_id = serializers.IntegerField(allow_null=True)
    username = serializers.CharField(allow_null=True)
    display_name = serializers.CharField(allow_null=True)
    avatar = ImageUrlField(allow_null=True)
    date_joined = serializers.DateTimeField(allow_null=True)
    wallet_balance = serializers.IntegerField(allow_null=True)
    orders_count = serializers.IntegerField()
    recent_orders = CustomerOrderSerializer(many=True)
    previous_conversations = serializers.IntegerField()


class DeskConversationSerializer(QueueItemSerializer):
    agent_typing = serializers.BooleanField(read_only=True)

    class Meta(QueueItemSerializer.Meta):
        fields = [
            *QueueItemSerializer.Meta.fields,
            "first_name",
            "last_name",
            "page_url",
            "agent_typing",
            "visitor_read_id",
            "agent_read_id",
            "rating_comment",
        ]
        read_only_fields = fields


class DeskSyncSerializer(serializers.Serializer):
    me = DeskMeSerializer()
    agents = AgentSerializer(many=True)
    queue = QueueItemSerializer(many=True)
    conversation = DeskConversationSerializer(allow_null=True)
    customer = CustomerSerializer(allow_null=True)
    messages = MessageSerializer(many=True)


class CannedReplySerializer(serializers.ModelSerializer):
    class Meta:
        model = CannedReply
        fields = ["id", "shortcut", "title", "body", "position"]
