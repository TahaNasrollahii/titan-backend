"""Single-elimination bracket generation with standard seeding and automatic byes."""

import math

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import ConflictError, DomainError
from apps.notifications.services import Kind, notify_many

from ..models import Match, Registration, RegistrationMember, Tournament
from . import results


def seeding_order(size: int) -> list[int]:
    """Seed numbers in bracket order so that 1 meets ``size``, 2 meets ``size-1``… and top seeds meet late.

    >>> seeding_order(8)
    [1, 8, 4, 5, 2, 7, 3, 6]
    """
    order = [1, 2]
    while len(order) < size:
        mirror = len(order) * 2 + 1
        order = [seed for current in order for seed in (current, mirror - current)]
    return order[:size]


def _bracket_size(entrants: int) -> int:
    return max(2, 2 ** math.ceil(math.log2(entrants)))


def _rating(registration: Registration) -> int:
    return registration.team.points if registration.team_id else registration.player.points


def _seed_entrants(tournament: Tournament) -> list[Registration]:
    entrants = list(
        tournament.registrations.filter(status=Registration.Status.CONFIRMED).select_related("team", "player")
    )
    entrants.sort(key=lambda r: (-_rating(r), r.confirmed_at or r.created_at))
    for seed, registration in enumerate(entrants, start=1):
        registration.seed = seed
    Registration.objects.bulk_update(entrants, ["seed"])
    return entrants


def _create_empty_bracket(tournament: Tournament, size: int) -> dict[tuple[int, int], Match]:
    """Create every match of every round, linked to the match its winner advances to."""
    rounds = int(math.log2(size))
    matches: dict[tuple[int, int], Match] = {}
    for round_number in range(rounds, 0, -1):  # build from the final backwards so next_match exists
        for position in range(size // 2**round_number):
            next_match = matches.get((round_number + 1, position // 2))
            matches[(round_number, position)] = Match.objects.create(
                tournament=tournament,
                round=round_number,
                position=position,
                best_of=tournament.best_of,
                next_match=next_match,
                next_slot=(
                    "" if next_match is None else (Match.Slot.A if position % 2 == 0 else Match.Slot.B)
                ),
            )
    return matches


@transaction.atomic
def generate_bracket(tournament: Tournament) -> list[Match]:
    """Seed confirmed entrants, build the bracket, resolve byes and put the tournament live."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    if tournament.state != Tournament.State.SCHEDULED:
        raise DomainError(
            _("The bracket can only be generated for a scheduled tournament."), code="invalid_state"
        )
    if tournament.matches.exists():
        raise ConflictError(_("The bracket has already been generated."), code="bracket_exists")

    # Unpaid registrations cannot play.
    tournament.registrations.filter(status=Registration.Status.PENDING_PAYMENT).update(
        status=Registration.Status.CANCELLED, updated_at=timezone.now()
    )
    entrants = _seed_entrants(tournament)
    if len(entrants) < 2:
        raise DomainError(
            _("At least two confirmed participants are required."), code="not_enough_participants"
        )

    size = _bracket_size(len(entrants))
    matches = _create_empty_bracket(tournament, size)
    by_seed = {registration.seed: registration for registration in entrants}
    order = seeding_order(size)

    for position in range(size // 2):
        match = matches[(1, position)]
        match.participant_a = by_seed.get(order[position * 2])
        match.participant_b = by_seed.get(order[position * 2 + 1])
        match.scheduled_at = tournament.starts_at
        match.status = Match.Status.SCHEDULED
        match.save(update_fields=["participant_a", "participant_b", "scheduled_at", "status", "updated_at"])
        if match.participant_b is None:  # standard seeding guarantees at most one bye per first-round match
            results.resolve_bye(match)

    tournament.state = Tournament.State.LIVE
    tournament.save(update_fields=["state", "updated_at"])
    _record_participation(tournament, entrants)
    return list(tournament.matches.order_by("round", "position"))


def _record_participation(tournament: Tournament, entrants: list[Registration]) -> None:
    for registration in entrants:
        results.bump_stats(registration, tournaments_played=1)
    users = [
        member.user
        for member in RegistrationMember.objects.filter(registration__in=entrants).select_related("user")
    ]
    notify_many(
        users,
        kind=Kind.TOURNAMENT,
        title=_("The bracket is live"),
        body=_("The bracket for %(tournament)s is ready. Check your first match.")
        % {"tournament": tournament.title},
        icon="trophy",
        data={"tournament_slug": tournament.slug},
    )
