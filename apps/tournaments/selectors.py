from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    Exists,
    F,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Sum,
    Value,
    When,
    Window,
)
from django.db.models.functions import Rank
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.accounts.ranks import tier_for_points

from .models import Match, PlayerStats, Registration, RegistrationMember, Season, TeamStats, Tournament

Status = Tournament.Status
ACTIVE_MATCH_STATUSES = (Match.Status.SCHEDULED, Match.Status.LIVE)


def annotate_status(queryset: QuerySet[Tournament]) -> QuerySet[Tournament]:
    """Add the public ``status`` (derived from ``state`` and the registration dates) as an annotation."""
    now = timezone.now()
    return queryset.annotate(
        status=Case(
            When(~Q(state=Tournament.State.SCHEDULED), then=F("state")),
            When(registration_opens_at__gt=now, then=Value(Status.UPCOMING)),
            When(registration_closes_at__gt=now, then=Value(Status.REGISTRATION_OPEN)),
            default=Value(Status.REGISTRATION_CLOSED),
            output_field=CharField(),
        )
    )


def _participant_prefetch(prefix: str = "") -> list[str]:
    return [f"{prefix}team", f"{prefix}player"]


def tournament_list(user: User | None) -> QuerySet[Tournament]:
    featured_matches = (
        Match.objects.filter(status__in=ACTIVE_MATCH_STATUSES)
        .select_related(*_participant_prefetch("participant_a__"), *_participant_prefetch("participant_b__"))
        .order_by("round", "scheduled_at", "position")
    )
    queryset = annotate_status(
        Tournament.objects.select_related("game", "season").annotate(
            participants_count=Count(
                "registrations", filter=Q(registrations__status=Registration.Status.CONFIRMED), distinct=True
            )
        )
    ).prefetch_related(Prefetch("matches", queryset=featured_matches, to_attr="active_matches"))
    return with_registration_flag(queryset, user)


def with_registration_flag(queryset: QuerySet[Tournament], user: User | None) -> QuerySet[Tournament]:
    if user is None or not user.is_authenticated:
        return queryset.annotate(is_registered=Value(False, output_field=BooleanField()))
    entries = RegistrationMember.objects.filter(
        user=user,
        registration__tournament=OuterRef("pk"),
        registration__status__in=Registration.ACTIVE_STATUSES,
    )
    return queryset.annotate(is_registered=Exists(entries))


def tournament_detail(user: User | None) -> QuerySet[Tournament]:
    return tournament_list(user).prefetch_related("prizes")


def user_registration(tournament: Tournament, user: User) -> Registration | None:
    if not user.is_authenticated:
        return None
    return (
        Registration.objects.active()
        .filter(tournament=tournament, members__user=user)
        .select_related("team", "player")
        .first()
    )


def confirmed_participants(tournament: Tournament) -> QuerySet[Registration]:
    return (
        tournament.registrations.filter(status=Registration.Status.CONFIRMED)
        .select_related("team", "player")
        .order_by(F("final_placement").asc(nulls_last=True), F("seed").asc(nulls_last=True), "confirmed_at")
    )


def bracket_matches(tournament: Tournament) -> QuerySet[Match]:
    return tournament.matches.select_related(
        *_participant_prefetch("participant_a__"), *_participant_prefetch("participant_b__")
    ).order_by("round", "position")


def round_name(round_number: int, total_rounds: int) -> str:
    remaining = total_rounds - round_number
    if remaining == 0:
        return _("Final")
    if remaining == 1:
        return _("Semi-finals")
    if remaining == 2:
        return _("Quarter-finals")
    return _("Round of %(count)d") % {"count": 2 ** (remaining + 1)}


def user_registration_ids(tournament: Tournament, user: User) -> set[int]:
    if not user.is_authenticated:
        return set()
    return set(
        RegistrationMember.objects.filter(user=user, registration__tournament=tournament).values_list(
            "registration_id", flat=True
        )
    )


def upcoming_matches() -> QuerySet[Match]:
    return (
        Match.objects.filter(status__in=ACTIVE_MATCH_STATUSES, tournament__state=Tournament.State.LIVE)
        .select_related(
            "tournament__game",
            *_participant_prefetch("participant_a__"),
            *_participant_prefetch("participant_b__"),
        )
        .order_by(F("scheduled_at").asc(nulls_last=True), "round")
    )


def my_registrations(user: User) -> QuerySet[Registration]:
    return (
        Registration.objects.filter(members__user=user)
        .exclude(status=Registration.Status.CANCELLED)
        .select_related("team", "player", "tournament__game", "tournament__season")
        .order_by("-tournament__starts_at")
    )


def current_stage(registration: Registration) -> str | None:
    """Human readable stage of a registration inside a live or finished bracket."""
    tournament = registration.tournament
    if tournament.state == Tournament.State.SCHEDULED:
        return None
    if registration.final_placement == 1:
        return _("Champion")
    if registration.final_placement:
        return _("Eliminated — place %(place)d") % {"place": registration.final_placement}
    total_rounds = tournament.matches.aggregate(rounds=Count("round", distinct=True))["rounds"] or 0
    match = (
        tournament.matches.filter(Q(participant_a=registration) | Q(participant_b=registration))
        .exclude(status=Match.Status.COMPLETED)
        .order_by("round")
        .first()
    )
    return round_name(match.round, total_rounds) if match else None


# ------------------------------------------------------------------ seasons & leaderboards
def current_season() -> Season | None:
    return Season.objects.filter(is_current=True).first() or Season.objects.order_by("-number").first()


def player_leaderboard(*, game_slug: str | None, season: Season | None) -> QuerySet[PlayerStats]:
    queryset = PlayerStats.objects.select_related("user", "game", "season")
    if game_slug:
        queryset = queryset.filter(game__slug=game_slug)
    if season:
        queryset = queryset.filter(season=season)
    return queryset.annotate(
        rank=Window(expression=Rank(), order_by=[F("points").desc(), F("wins").desc()])
    ).order_by("rank", "user__username")


def team_leaderboard(*, game_slug: str | None, season: Season | None) -> QuerySet[TeamStats]:
    queryset = TeamStats.objects.select_related("team", "game", "season").filter(
        team__dissolved_at__isnull=True
    )
    if game_slug:
        queryset = queryset.filter(game__slug=game_slug)
    if season:
        queryset = queryset.filter(season=season)
    return queryset.annotate(
        rank=Window(expression=Rank(), order_by=[F("points").desc(), F("wins").desc()])
    ).order_by("rank", "team__name")


def player_totals(user: User) -> dict:
    totals = PlayerStats.objects.filter(user=user).aggregate(
        matches=Sum("matches"),
        wins=Sum("wins"),
        losses=Sum("losses"),
        tournaments_played=Sum("tournaments_played"),
        tournaments_won=Sum("tournaments_won"),
        earnings_irt=Sum("earnings_irt"),
        earnings_usd=Sum("earnings_usd"),
    )
    totals = {key: value or 0 for key, value in totals.items()}
    totals["win_rate"] = round(totals["wins"] * 100 / totals["matches"], 1) if totals["matches"] else 0.0
    totals["points"] = user.points
    totals["rank"] = tier_for_points(user.points)
    return totals
