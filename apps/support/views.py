"""Live chat API.

Visitors (``support/chat/…``) authenticate with the ``X-Support-Token`` header they got when the chat
started. Staff (``support/desk/…``) use their normal JWT. Both sides poll one ``sync`` endpoint with
``after=<last message id>`` and receive only what changed.
"""

import logging

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404

logger = logging.getLogger(__name__)

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, mixins, status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from apps.core.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.core.permissions import IsStaff


class OptionalJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except Exception:
            return None

from . import selectors, services
from .models import CannedReply, Conversation, Message
from .serializers import (
    AgentSerializer,
    AssignInputSerializer,
    CannedReplySerializer,
    DeskConversationSerializer,
    DeskMeSerializer,
    DeskMessageInputSerializer,
    DeskSyncSerializer,
    MessageInputSerializer,
    MessageSerializer,
    PublicStatusSerializer,
    QueueItemSerializer,
    RateInputSerializer,
    ReadInputSerializer,
    StartInputSerializer,
    StartResponseSerializer,
    VisitorConversationSerializer,
    VisitorSyncSerializer,
)

TOKEN_HEADER = "X-Support-Token"
_after = OpenApiParameter("after", int, description="Only messages with a larger id.")
_token = OpenApiParameter(TOKEN_HEADER, str, OpenApiParameter.HEADER, required=True)


def _after_param(request) -> int:
    try:
        return max(0, int(request.query_params.get("after", 0)))
    except (TypeError, ValueError):
        return 0


def public_status() -> dict:
    try:
        agents = list(selectors.online_agents()[:5])
    except Exception as e:
        logger.warning("Failed to query online agents: %s", e)
        agents = []
    try:
        avg = services.average_response_seconds()
    except Exception as e:
        logger.warning("Failed to query average response seconds: %s", e)
        avg = None
    return {
        "online": bool(agents),
        "agents": agents,
        "avg_response_seconds": avg,
    }


# ------------------------------------------------------------------ throttles
class SupportTokenThrottle(SimpleRateThrottle):
    """Rate-limit a visitor by chat token, falling back to the IP before a chat exists."""

    def get_cache_key(self, request, view):
        token = request.headers.get(TOKEN_HEADER)
        ident = Conversation.hash_token(token) if token else self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class PollThrottle(SupportTokenThrottle):
    scope = "support_poll"


class SendThrottle(SupportTokenThrottle):
    scope = "support_send"


class StartThrottle(SimpleRateThrottle):
    scope = "support_start"

    def allow_request(self, request, view):
        if settings.DEBUG:
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


# ------------------------------------------------------------------ visitor
class VisitorView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [PollThrottle]

    def conversation(self) -> Conversation:
        return services.conversation_for_token(self.request.headers.get(TOKEN_HEADER))

    def sync_payload(self, conversation: Conversation, *, after: int = 0) -> dict:
        return {
            "conversation": conversation,
            "messages": selectors.public_messages(conversation, after=after),
            "support": public_status(),
        }


@extend_schema(tags=["support"], responses=PublicStatusSerializer)
class StatusView(APIView):
    """Whether anyone is answering right now (for the launcher), and the usual wait."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [PollThrottle]

    def get(self, request):
        return Response(PublicStatusSerializer(public_status(), context={"request": request}).data)


@extend_schema(tags=["support"], request=StartInputSerializer, responses={201: StartResponseSerializer})
class StartView(VisitorView):
    """Start a chat with a name and mobile number. Logged-in visitors are linked to their account."""

    authentication_classes = [OptionalJWTAuthentication]
    throttle_classes = [StartThrottle]

    def post(self, request):
        from apps.core.turnstile import validate_turnstile
        validate_turnstile(request)
        serializer = StartInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Exclude turnstile_token if present in validated_data
        clean_data = {k: v for k, v in serializer.validated_data.items() if k != "turnstile_token"}
        try:
            conversation, token = services.start_conversation(user=request.user, **clean_data)
            payload = {"token": token, **self.sync_payload(conversation)}
            return Response(
                StartResponseSerializer(payload, context={"request": request}).data,
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.exception("Failed to start support conversation: %s", e)
            raise


@extend_schema(tags=["support"], parameters=[_token, _after], responses=VisitorSyncSerializer)
class SyncView(VisitorView):
    """Poll for new messages, the agent's typing state and read receipts."""

    def get(self, request):
        conversation = self.conversation()
        services.touch_visitor(conversation)
        payload = self.sync_payload(conversation, after=_after_param(request))
        return Response(VisitorSyncSerializer(payload, context={"request": request}).data)


@extend_schema(
    tags=["support"],
    parameters=[_token],
    request={"application/json": MessageInputSerializer, "multipart/form-data": MessageInputSerializer},
    responses={201: MessageSerializer, 200: MessageSerializer},
)
class VisitorMessageView(VisitorView):
    """Send a message or an image. Resending the same ``clientId`` returns the stored message (200)."""

    throttle_classes = [SendThrottle]

    def post(self, request):
        conversation = self.conversation()
        serializer = MessageInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message, created = services.post_message(
            conversation, kind=Message.Kind.VISITOR, **serializer.validated_data
        )
        return Response(
            MessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(tags=["support"], parameters=[_token], request=ReadInputSerializer, responses={204: None})
class VisitorReadView(VisitorView):
    def post(self, request):
        serializer = ReadInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.mark_read(self.conversation(), side=Message.Kind.VISITOR, **serializer.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["support"], parameters=[_token], request=None, responses={204: None})
class VisitorTypingView(VisitorView):
    def post(self, request):
        services.set_typing(self.conversation(), side=Message.Kind.VISITOR)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["support"], parameters=[_token], request=None, responses=VisitorConversationSerializer)
class VisitorCloseView(VisitorView):
    def post(self, request):
        conversation = services.close(self.conversation())
        return Response(VisitorConversationSerializer(conversation, context={"request": request}).data)


@extend_schema(
    tags=["support"],
    parameters=[_token],
    request=RateInputSerializer,
    responses=VisitorConversationSerializer,
)
class VisitorRateView(VisitorView):
    throttle_classes = [SendThrottle]

    def post(self, request):
        serializer = RateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = services.rate(self.conversation(), **serializer.validated_data)
        return Response(VisitorConversationSerializer(conversation, context={"request": request}).data)


# ------------------------------------------------------------------ desk (staff)
class DeskView(APIView):
    permission_classes = [IsStaff]

    def get_conversation(self, pk) -> Conversation:
        return get_object_or_404(Conversation.objects.select_related("assignee__support_agent"), pk=pk)


@extend_schema(
    tags=["support-desk"],
    parameters=[
        OpenApiParameter("conversation", int, description="The open conversation, if any."),
        _after,
    ],
    responses=DeskSyncSerializer,
)
class DeskSyncView(DeskView):
    """Everything the desk polls for in one request: the queue, colleagues, and the open conversation.

    ``customer`` is only included on the first load of a conversation (``after=0``).
    """

    def get(self, request):
        agent = services.agent_profile(request.user)
        services.touch_agent(agent)
        after = _after_param(request)
        conversation = customer = None
        messages = Message.objects.none()
        conversation_id = request.query_params.get("conversation")
        if conversation_id and conversation_id.isdigit():
            conversation = selectors.conversations().filter(pk=conversation_id).first()
            if conversation:
                messages = selectors.desk_messages(conversation, after=after)
                customer = selectors.customer(conversation) if after == 0 else None
        payload = {
            "me": agent,
            "agents": selectors.staff_agents(),
            "queue": selectors.desk_queue(),
            "conversation": conversation,
            "customer": customer,
            "messages": messages,
        }
        return Response(DeskSyncSerializer(payload, context={"request": request}).data)


@extend_schema(tags=["support-desk"])
class DeskMeView(generics.RetrieveUpdateAPIView):
    """The signed-in agent's chat profile: public name, availability and SMS alerts."""

    serializer_class = DeskMeSerializer
    permission_classes = [IsStaff]
    http_method_names = ["get", "patch"]

    def get_object(self):
        return services.agent_profile(self.request.user)


@extend_schema(tags=["support-desk"], responses=AgentSerializer(many=True))
class DeskAgentsView(DeskView):
    def get(self, request):
        return Response(
            AgentSerializer(selectors.staff_agents(), many=True, context={"request": request}).data
        )


@extend_schema(
    tags=["support-desk"],
    parameters=[
        OpenApiParameter("status", str, enum=["open", "closed"]),
        OpenApiParameter("mine", bool),
        OpenApiParameter("search", str, description="Name, mobile number or ticket (T-1042)."),
    ],
)
class DeskConversationListView(generics.ListAPIView):
    """Conversation history for the desk's archive, newest first."""

    serializer_class = QueueItemSerializer
    permission_classes = [IsStaff]
    filter_backends: list = []

    def get_queryset(self):
        params = self.request.query_params
        queryset = selectors.conversations().order_by("-last_message_at", "-id")
        if params.get("status") in Conversation.Status.values:
            queryset = queryset.filter(status=params["status"])
        if params.get("mine") in {"1", "true"}:
            queryset = queryset.filter(assignee=self.request.user)
        search = (params.get("search") or "").strip()
        if search:
            query = (
                Q(first_name__icontains=search) | Q(last_name__icontains=search) | Q(phone__contains=search)
            )
            ticket = search.upper().removeprefix("T-")
            if ticket.isdigit() and int(ticket) > 1000:
                query |= Q(pk=int(ticket) - 1000)
            queryset = queryset.filter(query)
        return queryset


@extend_schema(
    tags=["support-desk"],
    request={
        "application/json": DeskMessageInputSerializer,
        "multipart/form-data": DeskMessageInputSerializer,
    },
    responses={201: MessageSerializer, 200: MessageSerializer},
)
class DeskMessageView(DeskView):
    """Reply (claims an unassigned conversation) or add an internal note (``internal: true``)."""

    def post(self, request, pk):
        conversation = self.get_conversation(pk)
        serializer = DeskMessageInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        kind = Message.Kind.NOTE if data.pop("internal") else Message.Kind.AGENT
        message, created = services.post_message(conversation, kind=kind, author=request.user, **data)
        return Response(
            MessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(tags=["support-desk"], request=AssignInputSerializer, responses=DeskConversationSerializer)
class DeskAssignView(DeskView):
    """Claim (``agent`` = yourself), transfer to a colleague, or return to the queue (``agent: null``)."""

    def post(self, request, pk):
        serializer = AssignInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.assign(self.get_conversation(pk), serializer.validated_data["agent"], by=request.user)
        conversation = selectors.conversations().get(pk=pk)
        return Response(DeskConversationSerializer(conversation, context={"request": request}).data)


@extend_schema(tags=["support-desk"], request=None, responses=DeskConversationSerializer)
class DeskCloseView(DeskView):
    def post(self, request, pk):
        services.close(self.get_conversation(pk), by=request.user)
        conversation = selectors.conversations().get(pk=pk)
        return Response(DeskConversationSerializer(conversation, context={"request": request}).data)


@extend_schema(tags=["support-desk"], request=ReadInputSerializer, responses={204: None})
class DeskReadView(DeskView):
    def post(self, request, pk):
        serializer = ReadInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.mark_read(self.get_conversation(pk), side=Message.Kind.AGENT, **serializer.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["support-desk"], request=None, responses={204: None})
class DeskTypingView(DeskView):
    def post(self, request, pk):
        services.set_typing(self.get_conversation(pk), side=Message.Kind.AGENT)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["support-desk"])
class CannedReplyViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Saved answers, inserted in the desk composer with ``/shortcut``."""

    queryset = CannedReply.objects.filter(is_active=True)
    serializer_class = CannedReplySerializer
    permission_classes = [IsStaff]
    pagination_class = None
    filter_backends: list = []
    http_method_names = ["get", "post", "patch", "delete"]
