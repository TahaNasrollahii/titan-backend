"""Read-only views that compose data from several apps for a single screen.

This module is the only part of ``core`` that depends on feature apps; nothing imports it except URLs.
"""

from django.core.cache import cache
from django.db.models import Q, Sum

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.catalog.models import Game, Product
from apps.catalog.selectors import games_with_counts
from apps.catalog.serializers import GameMiniSerializer, GameSerializer, ProductListSerializer
from apps.content.models import Announcement, Promo
from apps.content.serializers import AnnouncementSerializer, PromoSerializer
from apps.orders.models import Order
from apps.orders.serializers import OrderSerializer
from apps.payments.services import get_wallet
from apps.teams.models import Team
from apps.tournaments import selectors as tournament_selectors
from apps.tournaments.models import Registration, Tournament
from apps.tournaments.serializers import (
    MyTournamentSerializer,
    StatsTotalsSerializer,
    TournamentListSerializer,
    TournamentMiniSerializer,
)

SEARCH_LIMIT = 5
HERO_LIMIT = 3
RECENT_LIMIT = 5
PLATFORM_STATS_CACHE_KEY = "platform-stats"
PLATFORM_STATS_TTL = 10 * 60


class HomeSerializer(serializers.Serializer):
    hero_tournaments = TournamentListSerializer(many=True)
    hero_promos = PromoSerializer(many=True)
    categories = GameSerializer(many=True)
    announcements = AnnouncementSerializer(many=True)
    my_stats = StatsTotalsSerializer(allow_null=True)


@extend_schema(tags=["home"], responses=HomeSerializer)
class HomeView(APIView):
    """Everything the landing page needs in one request."""

    permission_classes = [AllowAny]

    def get(self, request):
        tournaments = tournament_selectors.tournament_list(request.user).exclude(
            state__in=[Tournament.State.COMPLETED, Tournament.State.CANCELLED]
        )
        hero = list(tournaments.filter(is_featured=True).order_by("starts_at")[:HERO_LIMIT])
        if not hero:
            hero = list(tournaments.order_by("starts_at")[:HERO_LIMIT])
        payload = {
            "hero_tournaments": hero,
            "hero_promos": Promo.objects.live().filter(placement=Promo.Placement.HOME_HERO),
            "categories": games_with_counts().filter(is_featured=True),
            "announcements": Announcement.objects.live(),
            "my_stats": tournament_selectors.player_totals(request.user)
            if request.user.is_authenticated
            else None,
        }
        return Response(HomeSerializer(payload, context={"request": request}).data)


class SearchSerializer(serializers.Serializer):
    games = GameMiniSerializer(many=True)
    tournaments = TournamentMiniSerializer(many=True)
    products = ProductListSerializer(many=True)


@extend_schema(tags=["home"], parameters=[OpenApiParameter("q", str)], responses=SearchSerializer)
class SearchView(APIView):
    """Top-bar search. Without ``q`` it returns popular picks."""

    permission_classes = [AllowAny]

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        games = games_with_counts()
        tournaments = Tournament.objects.select_related("game").exclude(state=Tournament.State.CANCELLED)
        products = Product.objects.active().select_related("game", "category").prefetch_related("platforms")

        if query:
            games = games.filter(Q(title__icontains=query) | Q(title_en__icontains=query))
            tournaments = tournaments.filter(Q(title__icontains=query) | Q(game__title_en__icontains=query))
            products = products.filter(Q(title__icontains=query) | Q(vendor__icontains=query))
        else:
            games = games.filter(is_featured=True)
            tournaments = tournaments.filter(is_featured=True)

        payload = {
            "games": games[:SEARCH_LIMIT],
            "tournaments": tournaments.order_by("starts_at")[:SEARCH_LIMIT],
            "products": products.order_by("-popularity")[:SEARCH_LIMIT],
        }
        return Response(SearchSerializer(payload, context={"request": request}).data)


class DashboardSerializer(serializers.Serializer):
    wallet_balance = serializers.IntegerField()
    tournaments_joined = serializers.IntegerField()
    active_teams = serializers.IntegerField()
    unread_notifications = serializers.IntegerField()
    recent_tournaments = MyTournamentSerializer(many=True)
    recent_orders = OrderSerializer(many=True)


@extend_schema(tags=["me"], responses=DashboardSerializer)
class DashboardView(APIView):
    """Overview tab of the user dashboard."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        registrations = tournament_selectors.my_registrations(user)
        recent = list(registrations[:RECENT_LIMIT])
        for entry in recent:
            entry.current_stage = tournament_selectors.current_stage(entry)
        payload = {
            "wallet_balance": get_wallet(user).balance,
            "tournaments_joined": registrations.filter(status=Registration.Status.CONFIRMED).count(),
            "active_teams": Team.objects.active().filter(memberships__user=user).count(),
            "unread_notifications": user.notifications.filter(is_read=False).count(),
            "recent_tournaments": recent,
            "recent_orders": Order.objects.filter(user=user).prefetch_related("items")[:RECENT_LIMIT],
        }
        return Response(DashboardSerializer(payload, context={"request": request}).data)


class PrizeTotalSerializer(serializers.Serializer):
    currency = serializers.CharField()
    amount = serializers.IntegerField()


class PlatformStatsSerializer(serializers.Serializer):
    players = serializers.IntegerField()
    teams = serializers.IntegerField()
    games = serializers.IntegerField()
    tournaments = serializers.IntegerField(help_text="Tournaments that are live or completed.")
    orders_delivered = serializers.IntegerField()
    prizes_awarded = PrizeTotalSerializer(many=True, help_text="Prize pools of completed tournaments.")


def platform_stats() -> dict:
    completed = Tournament.objects.filter(state=Tournament.State.COMPLETED)
    prizes = (
        completed.filter(prize_pool__gt=0)
        .values("prize_currency")
        .annotate(amount=Sum("prize_pool"))
        .order_by("prize_currency")
    )
    return {
        "players": User.objects.filter(is_active=True, is_staff=False).count(),
        "teams": Team.objects.active().count(),
        "games": Game.objects.filter(is_active=True).count(),
        "tournaments": Tournament.objects.filter(
            state__in=[Tournament.State.LIVE, Tournament.State.COMPLETED]
        ).count(),
        "orders_delivered": Order.objects.filter(status__in=Order.PAID_STATUSES).count(),
        # Amounts are never added across currencies.
        "prizes_awarded": [{"currency": row["prize_currency"], "amount": row["amount"]} for row in prizes],
    }


@extend_schema(tags=["home"], responses=PlatformStatsSerializer)
class PlatformStatsView(APIView):
    """Public platform-wide numbers for the About page. Cached briefly."""

    permission_classes = [AllowAny]

    def get(self, request):
        stats = cache.get_or_set(PLATFORM_STATS_CACHE_KEY, platform_stats, PLATFORM_STATS_TTL)
        return Response(PlatformStatsSerializer(stats).data)
