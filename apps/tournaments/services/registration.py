from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.core.exceptions import ConflictError, DomainError, ExternalServiceError, NotAllowedError
from apps.notifications.services import Kind, notify, notify_many
from apps.payments import registry
from apps.payments.models import Payment, PaymentMethod, WalletTransaction
from apps.payments.services import credit, pay_with_wallet, start_gateway_payment
from apps.teams.models import Team, TeamMembership
from apps.teams.services import teams_with_members

from ..models import Registration, RegistrationMember, Tournament


class RegistrationClosed(DomainError):
    default_detail = _("Registration for this tournament is not open.")
    default_code = "registration_closed"


class TournamentFull(ConflictError):
    default_detail = _("This tournament is full.")
    default_code = "tournament_full"


class AlreadyRegistered(ConflictError):
    default_detail = _("You are already registered in this tournament.")
    default_code = "already_registered"


class PaymentMethodRequired(DomainError):
    default_detail = _("Choose a payment method for the entry fee.")
    default_code = "payment_method_required"


@dataclass(frozen=True)
class RegistrationResult:
    registration: Registration
    payment_url: str | None = None


# ------------------------------------------------------------------ helpers
def confirmed_count(tournament: Tournament) -> int:
    return tournament.registrations.filter(status=Registration.Status.CONFIRMED).count()


def _lock_tournament(tournament: Tournament) -> Tournament:
    return Tournament.objects.select_for_update().get(pk=tournament.pk)


def _ensure_registration_open(tournament: Tournament) -> None:
    if tournament.public_status != Tournament.Status.REGISTRATION_OPEN:
        raise RegistrationClosed()


def _ensure_capacity(tournament: Tournament) -> None:
    if confirmed_count(tournament) >= tournament.max_participants:
        raise TournamentFull()


def _require_team_captain(team: Team, user: User) -> None:
    if not team.memberships.filter(user=user, role=TeamMembership.Role.CAPTAIN).exists():
        raise NotAllowedError(_("Only the team captain can do this."), code="not_captain")


def _team_lineup(tournament: Tournament, team: Team, actor: User, member_ids: list[int] | None) -> list[User]:
    """Validate the captain's pick: exactly ``team_size`` distinct members of ``team``."""
    if not team.is_active:
        raise DomainError(_("This team has been dissolved."), code="team_dissolved")
    _require_team_captain(team, actor)
    member_ids = member_ids or []
    if len(member_ids) != tournament.team_size or len(set(member_ids)) != len(member_ids):
        raise DomainError(
            _("Choose exactly %(count)d players for the lineup.") % {"count": tournament.team_size},
            code="lineup_size",
        )
    members = {
        m.user_id: m.user for m in team.memberships.select_related("user").filter(user_id__in=member_ids)
    }
    if len(members) != len(member_ids):
        raise DomainError(_("Every player in the lineup must be a member of the team."), code="not_member")
    return [members[pk] for pk in member_ids]


def _ensure_roster_available(
    tournament: Tournament, roster: list[User], exclude: Registration | None = None
) -> None:
    """A player takes part in a tournament at most once, whichever team they are in."""
    clashes = RegistrationMember.objects.filter(
        registration__tournament=tournament,
        registration__status__in=Registration.ACTIVE_STATUSES,
        user__in=roster,
    ).select_related("user")
    if exclude is not None:
        clashes = clashes.exclude(registration=exclude)
    clash = clashes.first()
    if clash is not None:
        raise AlreadyRegistered(
            _("%(player)s is already registered in this tournament.") % {"player": clash.user.display_name},
            code="roster_conflict",
        )


@transaction.atomic
def _create_registration(
    user: User, tournament: Tournament, team: Team | None, member_ids: list[int] | None
) -> Registration:
    tournament = _lock_tournament(tournament)
    _ensure_registration_open(tournament)
    _ensure_capacity(tournament)

    if tournament.is_team:
        if team is None:
            raise DomainError(_("Choose a team to register."), code="team_required")
        roster = _team_lineup(tournament, team, user, member_ids)
    else:
        if team is not None:
            raise DomainError(_("This is a solo tournament."), code="solo_tournament")
        roster = [user]
    _ensure_roster_available(tournament, roster)

    is_free = tournament.is_free
    try:
        with transaction.atomic():
            registration = Registration.objects.create(
                tournament=tournament,
                team=team if tournament.is_team else None,
                player=None if tournament.is_team else user,
                registered_by=user,
                status=Registration.Status.CONFIRMED if is_free else Registration.Status.PENDING_PAYMENT,
                confirmed_at=timezone.now() if is_free else None,
            )
    except IntegrityError as exc:
        raise AlreadyRegistered(_("This team is already registered in this tournament.")) from exc
    RegistrationMember.objects.bulk_create(
        RegistrationMember(registration=registration, user=u) for u in roster
    )
    if is_free:
        _notify_confirmed(registration)
    return registration


def _notify_confirmed(registration: Registration) -> None:
    users = [member.user for member in registration.members.select_related("user")]
    if registration.registered_by not in users:  # a captain who sits out still hears about it
        users.append(registration.registered_by)
    notify_many(
        users,
        kind=Kind.TOURNAMENT,
        title=_("Tournament registration confirmed"),
        body=_("Your registration in %(tournament)s is confirmed.")
        % {"tournament": registration.tournament.title},
        icon="trophy",
        data={"tournament_slug": registration.tournament.slug},
    )


# ------------------------------------------------------------------ public API
def register(
    user: User,
    tournament: Tournament,
    *,
    team: Team | None = None,
    members: list[int] | None = None,
    payment_method: str | None = None,
) -> RegistrationResult:
    """Register ``user`` (solo), or ``team`` with a lineup of ``members`` user ids (captain only).

    Paid tournaments require a payment method; the registrant pays one entry fee per registration.
    """
    if not tournament.is_free and payment_method not in PaymentMethod.values:
        raise PaymentMethodRequired()

    description = _("Entry fee for %(tournament)s") % {"tournament": tournament.title}
    if tournament.is_free or payment_method == PaymentMethod.WALLET:
        with transaction.atomic():
            registration = _create_registration(user, tournament, team, members)
            if not tournament.is_free:
                pay_with_wallet(
                    user,
                    amount=tournament.entry_fee,
                    purpose=Payment.Purpose.TOURNAMENT_REGISTRATION,
                    debit_kind=WalletTransaction.Kind.TOURNAMENT_FEE,
                    object_id=registration.pk,
                    reference=tournament.slug,
                    description=description,
                )
        registration.refresh_from_db()
        return RegistrationResult(registration=registration)

    registration = _create_registration(user, tournament, team, members)
    try:
        start = start_gateway_payment(
            user,
            amount=tournament.entry_fee,
            purpose=Payment.Purpose.TOURNAMENT_REGISTRATION,
            object_id=registration.pk,
            reference=tournament.slug,
            description=description,
        )
    except ExternalServiceError:
        Registration.objects.filter(pk=registration.pk).update(status=Registration.Status.CANCELLED)
        raise
    return RegistrationResult(registration=registration, payment_url=start.payment_url)


def _refund_entry(registration: Registration, amount: int, reason) -> None:
    if amount <= 0:
        return
    credit(
        registration.registered_by,
        amount,
        kind=WalletTransaction.Kind.REFUND,
        description=reason,
        reference=f"tournament:{registration.tournament.slug}",
    )


def _locked_registration_of(user: User, tournament: Tournament) -> Registration:
    found = Registration.objects.active().involving(user).filter(tournament=tournament).values("pk").first()
    if found is None:
        raise DomainError(_("You are not registered in this tournament."), code="not_registered")
    return Registration.objects.select_for_update().select_related("tournament", "team").get(pk=found["pk"])


def _require_manager(registration: Registration, user: User) -> None:
    """Solo entries are managed by the player, team entries by the team's current captain."""
    if registration.team_id:
        _require_team_captain(registration.team, user)
    elif registration.player_id != user.pk:
        raise NotAllowedError(_("Only the registered player can do this."), code="not_registrant")


@transaction.atomic
def withdraw(user: User, tournament: Tournament) -> Registration:
    """Leave a tournament before it starts. Paid entry fees are refunded to whoever paid them."""
    registration = _locked_registration_of(user, tournament)
    _require_manager(registration, user)
    if tournament.state != Tournament.State.SCHEDULED:
        raise DomainError(_("You can no longer withdraw from this tournament."), code="withdraw_closed")

    _refund_entry(registration, registration.entry_fee_paid, _("Tournament withdrawal refund"))
    registration.status = Registration.Status.WITHDRAWN
    registration.save(update_fields=["status", "updated_at"])
    return registration


@transaction.atomic
def update_lineup(user: User, tournament: Tournament, member_ids: list[int]) -> Registration:
    """Swap the players of a team registration: captain only, while registration is open."""
    tournament = _lock_tournament(tournament)
    registration = _locked_registration_of(user, tournament)
    if not registration.team_id:
        raise DomainError(_("This is a solo tournament."), code="solo_tournament")
    _ensure_registration_open(tournament)
    roster = _team_lineup(tournament, registration.team, user, member_ids)
    _ensure_roster_available(tournament, roster, exclude=registration)

    previous = set(registration.members.values_list("user_id", flat=True))
    registration.members.all().delete()
    RegistrationMember.objects.bulk_create(
        RegistrationMember(registration=registration, user=u) for u in roster
    )
    notify_many(
        [u for u in roster if u.pk not in previous],
        kind=Kind.TOURNAMENT,
        title=_("You are in the lineup"),
        body=_("%(team)s picked you for %(tournament)s.")
        % {"team": registration.team.name, "tournament": tournament.title},
        icon="trophy",
        data={"tournament_slug": tournament.slug},
    )
    return registration


# ------------------------------------------------------------------ payment handlers
def on_registration_paid(payment: Payment) -> None:
    registration = (
        Registration.objects.select_for_update().select_related("tournament").get(pk=payment.object_id)
    )
    tournament = _lock_tournament(registration.tournament)
    if registration.status != Registration.Status.PENDING_PAYMENT:
        _refund_entry(registration, payment.amount, _("Refund for an expired tournament registration"))
        return
    is_open = tournament.public_status == Tournament.Status.REGISTRATION_OPEN
    if not is_open or confirmed_count(tournament) >= tournament.max_participants:
        registration.status = Registration.Status.CANCELLED
        registration.save(update_fields=["status", "updated_at"])
        _refund_entry(registration, payment.amount, _("Refund: the tournament filled up before payment"))
        notify(
            registration.registered_by,
            kind=Kind.TOURNAMENT,
            title=_("Registration refunded"),
            body=_("%(tournament)s filled up; your entry fee was refunded to your wallet.")
            % {"tournament": tournament.title},
            icon="trophy",
        )
        return

    registration.status = Registration.Status.CONFIRMED
    registration.confirmed_at = timezone.now()
    registration.entry_fee_paid = payment.amount
    registration.payment = payment
    registration.save(update_fields=["status", "confirmed_at", "entry_fee_paid", "payment", "updated_at"])
    _notify_confirmed(registration)


def on_registration_payment_failed(payment: Payment) -> None:
    Registration.objects.filter(pk=payment.object_id, status=Registration.Status.PENDING_PAYMENT).update(
        status=Registration.Status.CANCELLED, updated_at=timezone.now()
    )


def register_payment_handlers() -> None:
    registry.register(
        Payment.Purpose.TOURNAMENT_REGISTRATION,
        registry.PurposeHandler(on_paid=on_registration_paid, on_failed=on_registration_payment_failed),
    )


def eligible_teams(user: User, tournament: Tournament):
    """Teams ``user`` captains that are not registered in ``tournament`` yet, with their members."""
    already = (
        Registration.objects.active().filter(tournament=tournament, team__isnull=False).values("team_id")
    )
    return (
        teams_with_members()
        .filter(memberships__user=user, memberships__role=TeamMembership.Role.CAPTAIN)
        .exclude(pk__in=already)
    )


def registered_players(tournament: Tournament) -> dict[int, str]:
    """``{user_id: entry name}`` for every player already taking part in ``tournament``."""
    entries = RegistrationMember.objects.filter(
        registration__tournament=tournament, registration__status__in=Registration.ACTIVE_STATUSES
    ).select_related("registration__team", "registration__player")
    return {entry.user_id: entry.registration.display_name for entry in entries}
