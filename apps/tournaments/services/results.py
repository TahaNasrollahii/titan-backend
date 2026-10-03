"""Match results: advancement, stats, placements, prizes and tournament completion."""

from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Badge, User, UserBadge
from apps.accounts.services import award_progress
from apps.core.exceptions import DomainError
from apps.notifications.services import Kind, notify_many
from apps.payments.models import WalletTransaction
from apps.payments.services import credit
from apps.teams.models import Team

from ..models import Currency, Match, PlayerStats, Registration, TeamStats, Tournament

MATCH_WIN_POINTS = 25
MATCH_WIN_XP = 120
MATCH_LOSS_XP = 40
CHAMPION_BADGE = "champion"


# ------------------------------------------------------------------ stats
def roster(registration: Registration) -> list[User]:
    return [member.user for member in registration.members.select_related("user")]


def bump_stats(registration: Registration, **increments: int) -> None:
    """Atomically add ``increments`` to the season stats of the registration's team and players."""
    tournament = registration.tournament
    keys = {"game_id": tournament.game_id, "season_id": tournament.season_id}
    updates = {field: F(field) + amount for field, amount in increments.items()}
    if registration.team_id:
        TeamStats.objects.get_or_create(team_id=registration.team_id, **keys)
        TeamStats.objects.filter(team_id=registration.team_id, **keys).update(**updates)
    for user in roster(registration):
        PlayerStats.objects.get_or_create(user=user, **keys)
        PlayerStats.objects.filter(user=user, **keys).update(**updates)


def _record_match(registration: Registration, *, won: bool) -> None:
    if won:
        bump_stats(registration, matches=1, wins=1, points=MATCH_WIN_POINTS)
    else:
        bump_stats(registration, matches=1, losses=1)
    if registration.team_id:
        Team.objects.filter(pk=registration.team_id).update(
            matches_played=F("matches_played") + 1,
            wins=F("wins") + int(won),
            losses=F("losses") + int(not won),
            points=F("points") + (MATCH_WIN_POINTS if won else 0),
        )
    for user in roster(registration):
        award_progress(user, xp=MATCH_WIN_XP if won else MATCH_LOSS_XP, points=MATCH_WIN_POINTS if won else 0)


# ------------------------------------------------------------------ advancement
def _advance(match: Match, winner: Registration) -> None:
    if match.next_match_id is None:
        return
    next_match = Match.objects.select_for_update().get(pk=match.next_match_id)
    if match.next_slot == Match.Slot.A:
        next_match.participant_a = winner
    else:
        next_match.participant_b = winner
    if (
        next_match.participant_a_id
        and next_match.participant_b_id
        and next_match.status == Match.Status.PENDING
    ):
        next_match.status = Match.Status.SCHEDULED
    next_match.save(update_fields=["participant_a", "participant_b", "status", "updated_at"])


def resolve_bye(match: Match) -> None:
    match.winner = match.participant_a or match.participant_b
    match.status = Match.Status.BYE
    match.completed_at = timezone.now()
    match.save(update_fields=["winner", "status", "completed_at", "updated_at"])
    _advance(match, match.winner)


def total_rounds(tournament: Tournament) -> int:
    return tournament.matches.aggregate(rounds=Max("round"))["rounds"] or 0


def placement_for_loser(round_number: int, rounds: int) -> int:
    """Losing the final → 2nd, a semi-final → 3rd, a quarter-final → 5th, …"""
    return 2 ** (rounds - round_number) + 1


# ------------------------------------------------------------------ reporting
@transaction.atomic
def update_match(match: Match, **fields) -> Match:
    """Staff edits that don't decide the match: schedule, lobby code, going live."""
    match = Match.objects.select_for_update().get(pk=match.pk)
    if match.status in (Match.Status.COMPLETED, Match.Status.BYE):
        raise DomainError(_("This match is already finished."), code="match_finished")
    if fields.get("status") == Match.Status.LIVE and not (match.participant_a_id and match.participant_b_id):
        raise DomainError(_("Both participants must be known first."), code="match_not_ready")
    for name, value in fields.items():
        setattr(match, name, value)
    match.save()
    return match


@transaction.atomic
def report_result(match: Match, *, score_a: int, score_b: int) -> Match:
    match = Match.objects.select_for_update().select_related("tournament").get(pk=match.pk)
    if match.tournament.state != Tournament.State.LIVE:
        raise DomainError(_("The tournament is not live."), code="tournament_not_live")
    if match.status in (Match.Status.COMPLETED, Match.Status.BYE):
        raise DomainError(_("This match is already finished."), code="match_finished")
    if not (match.participant_a_id and match.participant_b_id):
        raise DomainError(_("Both participants must be known first."), code="match_not_ready")
    if score_a == score_b:
        raise DomainError(_("Knockout matches cannot end in a draw."), code="draw_not_allowed")

    winner, loser = (
        (match.participant_a, match.participant_b)
        if score_a > score_b
        else (match.participant_b, match.participant_a)
    )
    match.score_a, match.score_b = score_a, score_b
    match.winner = winner
    match.status = Match.Status.COMPLETED
    match.completed_at = timezone.now()
    match.save(update_fields=["score_a", "score_b", "winner", "status", "completed_at", "updated_at"])

    _record_match(winner, won=True)
    _record_match(loser, won=False)
    loser.final_placement = placement_for_loser(match.round, total_rounds(match.tournament))
    loser.save(update_fields=["final_placement", "updated_at"])

    if match.next_match_id:
        _advance(match, winner)
    else:
        complete_tournament(match.tournament, champion=winner)
    return match


# ------------------------------------------------------------------ completion & prizes
def _award_prize(registration: Registration, amount: int, points: int, currency: str) -> None:
    players = roster(registration)
    if not players:
        return
    share = amount // len(players)
    earnings_field = "earnings_irt" if currency == Currency.IRT else "earnings_usd"
    tournament = registration.tournament
    keys = {"game_id": tournament.game_id, "season_id": tournament.season_id}

    if registration.team_id and (amount or points):
        TeamStats.objects.filter(team_id=registration.team_id, **keys).update(
            points=F("points") + points, **{earnings_field: F(earnings_field) + amount}
        )
        Team.objects.filter(pk=registration.team_id).update(points=F("points") + points)
    for user in players:
        PlayerStats.objects.filter(user=user, **keys).update(
            points=F("points") + points, **{earnings_field: F(earnings_field) + share}
        )
        if points:
            award_progress(user, points=points)
        if share and currency == Currency.IRT:
            credit(
                user,
                share,
                kind=WalletTransaction.Kind.PRIZE,
                description=_("Prize from %(tournament)s") % {"tournament": tournament.title},
                reference=f"tournament:{tournament.slug}",
            )


def _award_prizes(tournament: Tournament) -> None:
    for prize in tournament.prizes.all():
        winners = list(
            tournament.registrations.filter(
                status=Registration.Status.CONFIRMED, final_placement=prize.place
            ).select_related("tournament")
        )
        if not winners:
            continue
        for registration in winners:  # tied places (e.g. both semi-final losers) split the prize
            _award_prize(registration, prize.amount // len(winners), prize.points, tournament.prize_currency)


def _award_champion_badge(champion: Registration) -> None:
    badge = Badge.objects.filter(slug=CHAMPION_BADGE).first()
    if badge is None:
        return
    for user in roster(champion):
        UserBadge.objects.get_or_create(user=user, badge=badge)


def complete_tournament(tournament: Tournament, *, champion: Registration) -> None:
    champion.final_placement = 1
    champion.save(update_fields=["final_placement", "updated_at"])
    bump_stats(champion, tournaments_won=1)

    tournament.state = Tournament.State.COMPLETED
    tournament.ends_at = max(tournament.starts_at, timezone.now())  # actual end time
    tournament.save(update_fields=["state", "ends_at", "updated_at"])

    _award_prizes(tournament)
    _award_champion_badge(champion)
    for registration in tournament.registrations.filter(status=Registration.Status.CONFIRMED):
        notify_many(
            roster(registration),
            kind=Kind.TOURNAMENT,
            title=_("Tournament finished"),
            body=_("%(tournament)s has finished. Your final place: %(place)s.")
            % {"tournament": tournament.title, "place": registration.final_placement or "—"},
            icon="trophy",
            data={"tournament_slug": tournament.slug, "placement": registration.final_placement},
        )
