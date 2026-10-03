from django.conf import settings
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404

from drf_spectacular.utils import extend_schema
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .models import Friendship, GameAccount, OTPCode, RankTier, User, UserBadge
from .serializers import (
    AddFriendSerializer,
    AuthTokensSerializer,
    AvatarSerializer,
    FriendRequestSerializer,
    GameAccountSerializer,
    HeartbeatSerializer,
    MeSerializer,
    OTPRequestResponseSerializer,
    OTPVerifySerializer,
    PhoneSerializer,
    PresenceUserSerializer,
    RankTierSerializer,
    RefreshTokenSerializer,
)


def user_with_badges():
    return User.objects.select_related("favorite_game", "current_game").prefetch_related(
        Prefetch("user_badges", queryset=UserBadge.objects.select_related("badge"))
    )


def _otp_window_response(phone: str) -> Response:
    payload = {
        "phone": phone,
        "expires_in": settings.OTP_TTL_SECONDS,
        "resend_in": settings.OTP_RESEND_COOLDOWN_SECONDS,
    }
    return Response(OTPRequestResponseSerializer(payload).data, status=status.HTTP_201_CREATED)


# ------------------------------------------------------------------ auth
class OTPThrottleMixin:
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_ip"


@extend_schema(tags=["auth"], request=PhoneSerializer, responses={201: OTPRequestResponseSerializer})
class OTPRequestView(OTPThrottleMixin, APIView):
    """Send a login code to a mobile number (registers the number on first verify)."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        serializer = PhoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone"]
        services.request_otp(phone, OTPCode.Purpose.LOGIN)
        return _otp_window_response(phone)


@extend_schema(tags=["auth"], request=OTPVerifySerializer, responses=AuthTokensSerializer)
class OTPVerifyView(OTPThrottleMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, created = services.login_with_otp(**serializer.validated_data)
        tokens = services.issue_tokens(user)
        user = user_with_badges().get(pk=user.pk)
        payload = {
            **tokens,
            "is_new_user": created,
            "user": MeSerializer(user, context={"request": request}).data,
        }
        return Response(payload)


@extend_schema(tags=["auth"], request=RefreshTokenSerializer, responses={204: None})
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RefreshTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.logout(serializer.validated_data["refresh"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ me
@extend_schema(tags=["me"])
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = MeSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch"]

    def get_object(self):
        return user_with_badges().get(pk=self.request.user.pk)


@extend_schema(tags=["me"], request={"multipart/form-data": AvatarSerializer}, responses=MeSerializer)
class AvatarView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = AvatarSerializer(request.user, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        user = user_with_badges().get(pk=request.user.pk)
        return Response(MeSerializer(user, context={"request": request}).data)


@extend_schema(tags=["me"], request=PhoneSerializer, responses={201: OTPRequestResponseSerializer})
class PhoneChangeRequestView(OTPThrottleMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PhoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone"]
        services.request_otp(phone, OTPCode.Purpose.CHANGE_PHONE)
        return _otp_window_response(phone)


@extend_schema(tags=["me"], request=OTPVerifySerializer, responses=MeSerializer)
class PhoneChangeVerifyView(OTPThrottleMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.change_phone(
            request.user, serializer.validated_data["phone"], serializer.validated_data["code"]
        )
        user = user_with_badges().get(pk=request.user.pk)
        return Response(MeSerializer(user, context={"request": request}).data)


@extend_schema(tags=["me"], request=HeartbeatSerializer, responses={204: None})
class HeartbeatView(APIView):
    """Report presence (online / away / in game). Clients should call this every ~60 seconds."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = HeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.heartbeat(
            request.user,
            status=serializer.validated_data["status"],
            game=serializer.validated_data.get("game"),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["me"])
class GameAccountViewSet(viewsets.ModelViewSet):
    queryset = GameAccount.objects.none()  # model hint for the schema; real queryset is per-user
    serializer_class = GameAccountSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    filter_backends: list = []

    def get_queryset(self):
        return GameAccount.objects.filter(user=self.request.user).select_related("game")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ------------------------------------------------------------------ friends
@extend_schema(tags=["social"])
class FriendViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Accepted friends with live presence, plus adding and removing friends by game ID."""

    permission_classes = [IsAuthenticated]
    pagination_class = None
    filter_backends: list = []
    lookup_field = "username"
    lookup_value_regex = r"[A-Za-z0-9_.]+"

    def get_serializer_class(self):
        return AddFriendSerializer if self.action == "create" else PresenceUserSerializer

    def get_queryset(self):
        return services.friends_of(self.request.user).order_by("username")

    @extend_schema(request=AddFriendSerializer, responses={201: FriendRequestSerializer})
    def create(self, request):
        serializer = AddFriendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        friendship = services.send_friend_request(request.user, serializer.validated_data["username"])
        data = FriendRequestSerializer(friendship, context={"request": request}).data
        return Response(data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={204: None})
    def destroy(self, request, username=None):
        other = get_object_or_404(User, username__iexact=username)
        services.remove_friend(request.user, other)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["social"])
class FriendRequestViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Incoming pending friend requests."""

    queryset = Friendship.objects.none()  # model hint for the schema; real queryset is per-user
    serializer_class = FriendRequestSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    filter_backends: list = []

    def get_queryset(self):
        return Friendship.objects.filter(
            to_user=self.request.user, status=Friendship.Status.PENDING
        ).select_related("from_user", "to_user")

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        friendship = services.accept_friend_request(request.user, int(pk))
        return Response(self.get_serializer(friendship).data)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        friendship = services.decline_friend_request(request.user, int(pk))
        return Response(self.get_serializer(friendship).data)


# ------------------------------------------------------------------ reference
@extend_schema(tags=["players"])
class RankTierListView(generics.ListAPIView):
    queryset = RankTier.objects.all()
    serializer_class = RankTierSerializer
    permission_classes = [AllowAny]
    pagination_class = None
    filter_backends: list = []
