from itertools import groupby

from django.shortcuts import get_object_or_404

import django_filters
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.views import user_with_badges
from apps.core.permissions import IsStaff
from apps.teams.models import Team
from apps.teams.serializers import TeamListSerializer

from . import selectors
from .models import Match, PlayerStats, Registration, Season, Tournament
from .serializers import (
    BracketRoundSerializer,
    MatchSerializer,
    MatchUpdateSerializer,
    MyTournamentSerializer,
    ParticipantSerializer,
    PlayerLeaderboardSerializer,
    PlayerProfileSerializer,
    RegisterSerializer,
    RegistrationResultSerializer,
    SeasonSerializer,
    StatsTotalsSerializer,
    TeamLeaderboardSerializer,
    TeamTournamentSerializer,
    TournamentDetailSerializer,
    TournamentListSerializer,
    UpcomingMatchSerializer,
)
from .services import bracket, registration, results


class TournamentFilter(django_filters.FilterSet):
    game = django_filters.CharFilter(field_name="game__slug")
    status = django_filters.MultipleChoiceFilter(choices=Tournament.Status.choices)
    participant_type = django_filters.ChoiceFilter(choices=Tournament.ParticipantType.choices)
    region = django_filters.ChoiceFilter(
        field_name="region", choices=Tournament._meta.get_field("region").choices
    )
    featured = django_filters.BooleanFilter(field_name="is_featured")
    free = django_filters.BooleanFilter(method="filter_free")

    class Meta:
        model = Tournament
        fields = ["game", "status", "participant_type", "region", "featured", "free"]

    def filter_free(self, queryset, name, value):
        return queryset.filter(entry_fee=0) if value else queryset.filter(entry_fee__gt=0)


@extend_schema(tags=["tournaments"])
class TournamentViewSet(viewsets.ReadOnlyModelViewSet):
    """Tournaments. ``ordering``: ``starts_at`` (soonest, default), ``-prize_pool``, ``-starts_at``."""

    permission_classes = [AllowAny]
    lookup_field = "slug"
    filterset_class = TournamentFilter
    search_fields = ["title", "game__title", "game__title_en"]
    ordering_fields = ["starts_at", "prize_pool", "entry_fee"]
    ordering = ["starts_at"]

    def get_queryset(self):
        if self.action == "retrieve":
            return selectors.tournament_detail(self.request.user)
        return selectors.tournament_list(self.request.user)

    def get_serializer_class(self):
        return TournamentDetailSerializer if self.action == "retrieve" else TournamentListSerializer

    def retrieve(self, request, *args, **kwargs):
        tournament = self.get_object()
        context = {
            **self.get_serializer_context(),
            "my_registration": selectors.user_registration(tournament, request.user),
        }
        return Response(TournamentDetailSerializer(tournament, context=context).data)

    def _tournament(self, slug: str) -> Tournament:
        return get_object_or_404(Tournament, slug=slug)

    @extend_schema(responses=ParticipantSerializer(many=True))
    @action(detail=True, methods=["get"], filter_backends=[], pagination_class=None)
    def participants(self, request, slug=None):
        participants = selectors.confirmed_participants(self._tournament(slug))
        return Response(ParticipantSerializer(participants, many=True, context={"request": request}).data)

    @extend_schema(responses=BracketRoundSerializer(many=True))
    @action(detail=True, methods=["get"], filter_backends=[], pagination_class=None)
    def bracket(self, request, slug=None):
        tournament = self._tournament(slug)
        matches = list(selectors.bracket_matches(tournament))
        total = matches[-1].round if matches else 0
        rounds = [
            {"round": number, "name": selectors.round_name(number, total), "matches": list(items)}
            for number, items in groupby(matches, key=lambda match: match.round)
        ]
        context = {
            "request": request,
            "my_registration_ids": selectors.user_registration_ids(tournament, request.user),
        }
        return Response(BracketRoundSerializer(rounds, many=True, context=context).data)

    @extend_schema(responses=TeamListSerializer(many=True))
    @action(
        detail=True,
        methods=["get"],
        url_path="eligible-teams",
        permission_classes=[IsAuthenticated],
        filter_backends=[],
        pagination_class=None,
    )
    def eligible_teams(self, request, slug=None):
        teams = registration.eligible_teams(request.user, self._tournament(slug))
        return Response(TeamListSerializer(teams, many=True, context={"request": request}).data)

    @extend_schema(
        methods=["post"], request=RegisterSerializer, responses={201: RegistrationResultSerializer}
    )
    @extend_schema(methods=["delete"], request=None, responses={204: None})
    @action(detail=True, methods=["post", "delete"], permission_classes=[IsAuthenticated])
    def register(self, request, slug=None):
        tournament = self._tournament(slug)
        if request.method == "DELETE":
            registration.withdraw(request.user, tournament)
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = registration.register(
            request.user,
            tournament,
            team=serializer.validated_data.get("team"),
            payment_method=serializer.validated_data.get("payment_method"),
        )
        payload = {"registration": result.registration, "payment_url": result.payment_url}
        data = RegistrationResultSerializer(payload, context={"request": request}).data
        return Response(data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses={201: BracketRoundSerializer(many=True)})
    @action(detail=True, methods=["post"], url_path="generate-bracket", permission_classes=[IsStaff])
    def generate_bracket(self, request, slug=None):
        bracket.generate_bracket(self._tournament(slug))
        response = self.bracket(request, slug=slug)
        response.status_code = status.HTTP_201_CREATED
        return response


@extend_schema(tags=["tournaments"])
class MatchViewSet(mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Staff-only match management. Sending both scores reports the result and advances the winner."""

    queryset = Match.objects.select_related("tournament")
    permission_classes = [IsStaff]
    http_method_names = ["patch"]
    serializer_class = MatchUpdateSerializer

    @extend_schema(request=MatchUpdateSerializer, responses=MatchSerializer)
    def partial_update(self, request, *args, **kwargs):
        match = self.get_object()
        serializer = MatchUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        scores = {key: data.pop(key) for key in ("score_a", "score_b") if key in data}
        if data:
            match = results.update_match(match, **data)
        if scores:
            match = results.report_result(match, **scores)
        match = selectors.bracket_matches(match.tournament).get(pk=match.pk)
        return Response(MatchSerializer(match, context={"request": request}).data)


@extend_schema(tags=["tournaments"], parameters=[OpenApiParameter("game", str)])
class UpcomingMatchListView(generics.ListAPIView):
    serializer_class = UpcomingMatchSerializer
    permission_classes = [AllowAny]
    filter_backends: list = []

    def get_queryset(self):
        queryset = selectors.upcoming_matches()
        game = self.request.query_params.get("game")
        return queryset.filter(tournament__game__slug=game) if game else queryset


@extend_schema(tags=["tournaments"])
class MyTournamentListView(generics.ListAPIView):
    """The current user's tournament registrations, including the current bracket stage."""

    serializer_class = MyTournamentSerializer
    permission_classes = [IsAuthenticated]
    filter_backends: list = []

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(selectors.my_registrations(request.user))
        for entry in page:
            entry.current_stage = selectors.current_stage(entry)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


@extend_schema(tags=["teams"])
class TeamTournamentListView(generics.ListAPIView):
    serializer_class = TeamTournamentSerializer
    permission_classes = [AllowAny]
    filter_backends: list = []

    def get_queryset(self):
        team = get_object_or_404(Team, pk=self.kwargs["team_id"])
        return (
            Registration.objects.filter(team=team)
            .exclude(status=Registration.Status.CANCELLED)
            .select_related("team", "tournament__game")
            .order_by("-tournament__starts_at")
        )


@extend_schema(tags=["tournaments"])
class SeasonListView(generics.ListAPIView):
    queryset = Season.objects.all()
    serializer_class = SeasonSerializer
    permission_classes = [AllowAny]
    pagination_class = None
    filter_backends: list = []


# ------------------------------------------------------------------ leaderboards & stats
_leaderboard_params = [
    OpenApiParameter("game", str, description="Game slug."),
    OpenApiParameter("season", int, description="Season number; defaults to the current season."),
]


class LeaderboardMixin:
    permission_classes = [AllowAny]
    filter_backends: list = []

    def season(self) -> Season | None:
        number = self.request.query_params.get("season")
        if number and number.isdigit():
            return get_object_or_404(Season, number=int(number))
        return selectors.current_season()


@extend_schema(tags=["leaderboards"], parameters=_leaderboard_params)
class PlayerLeaderboardView(LeaderboardMixin, generics.ListAPIView):
    serializer_class = PlayerLeaderboardSerializer

    def get_queryset(self):
        return selectors.player_leaderboard(
            game_slug=self.request.query_params.get("game"), season=self.season()
        )


@extend_schema(tags=["leaderboards"], parameters=_leaderboard_params)
class TeamLeaderboardView(LeaderboardMixin, generics.ListAPIView):
    serializer_class = TeamLeaderboardSerializer

    def get_queryset(self):
        return selectors.team_leaderboard(
            game_slug=self.request.query_params.get("game"), season=self.season()
        )


@extend_schema(tags=["me"], responses=StatsTotalsSerializer)
class MyStatsView(APIView):
    """Totals for the "your score" widget: games, wins, losses, points and rank."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(StatsTotalsSerializer(selectors.player_totals(request.user)).data)


@extend_schema(tags=["players"], responses=PlayerProfileSerializer)
class PlayerProfileView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, username: str):
        player = get_object_or_404(user_with_badges().filter(is_active=True), username__iexact=username)
        payload = {
            "player": player,
            "totals": selectors.player_totals(player),
            "games": PlayerStats.objects.filter(user=player).select_related("game", "season"),
            "teams": Team.objects.active().filter(memberships__user=player),
        }
        return Response(PlayerProfileSerializer(payload, context={"request": request}).data)
