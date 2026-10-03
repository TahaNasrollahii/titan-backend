from django.urls import path

from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = SimpleRouter()
router.register("me/game-accounts", views.GameAccountViewSet, basename="game-account")
router.register("me/friends", views.FriendViewSet, basename="friend")
router.register("me/friend-requests", views.FriendRequestViewSet, basename="friend-request")

urlpatterns = [
    path("auth/otp/request/", views.OTPRequestView.as_view(), name="otp-request"),
    path("auth/otp/verify/", views.OTPVerifyView.as_view(), name="otp-verify"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
    path("me/", views.MeView.as_view(), name="me"),
    path("me/avatar/", views.AvatarView.as_view(), name="me-avatar"),
    path("me/phone/change/request/", views.PhoneChangeRequestView.as_view(), name="phone-change-request"),
    path("me/phone/change/verify/", views.PhoneChangeVerifyView.as_view(), name="phone-change-verify"),
    path("me/heartbeat/", views.HeartbeatView.as_view(), name="heartbeat"),
    path("ranks/", views.RankTierListView.as_view(), name="rank-list"),
    *router.urls,
]
