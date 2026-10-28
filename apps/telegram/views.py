"""Telegram Mini App sign-in and the bot webhook.

The Mini App never stores a password or refresh cookie: on every launch it trades the launch data Telegram
signed for a fresh token pair. The first launch links the Telegram user to a Titan account by phone number.
"""

import hmac
import logging

from django.conf import settings
from django.http import Http404

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers import MeSerializer, OTPRequestResponseSerializer
from apps.accounts.services import issue_tokens
from apps.accounts.views import user_with_badges
from apps.core.throttling import ScopedRateThrottle

from . import services
from .initdata import InitData, parse_init_data
from .models import TelegramAccount
from .serializers import (
    CodeRequestSerializer,
    CodeVerifySerializer,
    ContactLinkSerializer,
    InitDataSerializer,
    TelegramAccountSerializer,
    TelegramSignInSerializer,
)

logger = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def _telegram_user(init_data: InitData) -> dict:
    tg = init_data.user
    return {
        "id": tg.id,
        "first_name": tg.first_name,
        "last_name": tg.last_name,
        "username": tg.username,
        "photo_url": tg.photo_url,
    }


def _signed_in(request, init_data: InitData, account: TelegramAccount, *, created: bool) -> Response:
    tokens = issue_tokens(account.user)
    user = user_with_badges().get(pk=account.user_id)
    payload = {
        "linked": True,
        "telegram_user": _telegram_user(init_data),
        **tokens,
        "is_new_user": created,
        "user": MeSerializer(user, context={"request": request}).data,
        "telegram": TelegramAccountSerializer(account).data,
    }
    return Response(payload)


class TelegramAuthThrottle(ScopedRateThrottle):
    scope = "telegram_auth"


class PublicTelegramView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [TelegramAuthThrottle]

    def init_data(self, serializer) -> InitData:
        return parse_init_data(serializer.validated_data["init_data"])


@extend_schema(tags=["telegram"], request=InitDataSerializer, responses=TelegramSignInSerializer)
class TelegramAuthView(PublicTelegramView):
    """Sign in with the Mini App's launch data. ``linked: false`` means the phone number is needed first."""

    def post(self, request):
        serializer = InitDataSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        init_data = self.init_data(serializer)
        account = services.sign_in(init_data)
        if account is None:
            return Response({"linked": False, "telegram_user": _telegram_user(init_data)})
        return _signed_in(request, init_data, account, created=False)


@extend_schema(tags=["telegram"], request=ContactLinkSerializer, responses=TelegramSignInSerializer)
class TelegramContactView(PublicTelegramView):
    """Link the Telegram user to the Titan account of the phone number they shared, then sign in."""

    def post(self, request):
        serializer = ContactLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        init_data = self.init_data(serializer)
        account, created = services.link_with_contact(init_data, serializer.validated_data["contact"])
        return _signed_in(request, init_data, account, created=created)


class TelegramCodeThrottle(ScopedRateThrottle):
    scope = "otp_ip"


@extend_schema(
    tags=["telegram"], request=CodeRequestSerializer, responses={201: OTPRequestResponseSerializer}
)
class TelegramCodeRequestView(PublicTelegramView):
    """Text a code to a Titan mobile number (for users whose Telegram number is a different one)."""

    throttle_classes = [TelegramAuthThrottle, TelegramCodeThrottle]

    def post(self, request):
        serializer = CodeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.init_data(serializer)  # only real Telegram users may trigger texts
        phone = serializer.validated_data["phone"]
        services.request_link_code(phone)
        payload = {
            "phone": phone,
            "expires_in": settings.OTP_TTL_SECONDS,
            "resend_in": settings.OTP_RESEND_COOLDOWN_SECONDS,
        }
        return Response(OTPRequestResponseSerializer(payload).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["telegram"], request=CodeVerifySerializer, responses=TelegramSignInSerializer)
class TelegramCodeVerifyView(PublicTelegramView):
    throttle_classes = [TelegramAuthThrottle, TelegramCodeThrottle]

    def post(self, request):
        serializer = CodeVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        init_data = self.init_data(serializer)
        data = serializer.validated_data
        account, created = services.link_with_code(init_data, data["phone"], data["code"])
        return _signed_in(request, init_data, account, created=created)


@extend_schema(tags=["telegram"])
class TelegramAccountView(APIView):
    """The signed-in user's Telegram link: bot notification settings, or unlinking (DELETE)."""

    permission_classes = [IsAuthenticated]
    serializer_class = TelegramAccountSerializer

    def account(self) -> TelegramAccount:
        account = TelegramAccount.objects.filter(user=self.request.user).first()
        if account is None:
            raise Http404
        return account

    def get(self, request):
        return Response(TelegramAccountSerializer(self.account()).data)

    def patch(self, request):
        account = self.account()
        serializer = TelegramAccountSerializer(account, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.update_settings(
            account,
            notifications_enabled=serializer.validated_data.get("notifications_enabled"),
            write_access_granted=serializer.validated_data.get("write_access_granted"),
        )
        return Response(TelegramAccountSerializer(account).data)

    @extend_schema(responses={204: None})
    def delete(self, request):
        services.unlink(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(exclude=True)
class TelegramWebhookView(APIView):
    """Bot updates from Telegram. Authenticated by the secret token set with ``setWebhook``."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes: list = []
    # Telegram's payloads are snake_case already and replies must stay snake_case.
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def post(self, request):
        secret = settings.TELEGRAM_WEBHOOK_SECRET
        received = request.headers.get(SECRET_HEADER, "")
        if not secret or not hmac.compare_digest(secret.encode(), received.encode()):
            raise Http404
        if not isinstance(request.data, dict):
            return Response(status=status.HTTP_204_NO_CONTENT)
        try:
            reply = services.handle_update(request.data)
        except Exception:
            # A failing update would be redelivered forever; log it and acknowledge.
            logger.exception("Telegram update %s failed", request.data.get("update_id"))
            reply = None
        return Response(reply) if reply else Response(status=status.HTTP_204_NO_CONTENT)
