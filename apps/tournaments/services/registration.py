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


def _roster_for_team(tournament: Tournament, team: Team, actor: User) -> list[User]:
    if not team.is_active:
        raise DomainError(_("This team has been dissolved."), code="team_dissolved")
    if team.game_id != tournament.game_id:
        raise DomainError(_("The team's game does not match this tournament."), code="team_game_mismatch")
    memberships = list(team.memberships.select_related("user"))
    if not any(m.user_id == actor.pk and m.role == TeamMembership.Role.CAPTAIN for m in memberships):
        raise NotAllowedError(_("Only the team captain can register the team."), code="not_captain")
    if len(memberships) < tournament.team_size:
        raise DomainError(
            _("The team needs at least %(count)d members for this tournament.")
            % {"count": tournament.team_size},
            code="team_too_small",
        )
    return [m.user for m in memberships]


def _ensure_roster_available(tournament: Tournament, roster: list[User]) -> None:
    clash = RegistrationMember.objects.filter(
        registration__tournament=tournament,
        registration__status__in=Registration.ACTIVE_STATUSES,
        user__in=roster,
    ).exists()
    if clash:
        raise AlreadyRegistered(
            _("A player in this roster is already registered in this tournament."), code="roster_conflict"
        )


@transaction.atomic
def _create_registration(user: User, tournament: Tournament, team: Team | None) -> Registration:
    tournament = _lock_tournament(tournament)
    _ensure_registration_open(tournament)
    _ensure_capacity(tournament)

    if tournament.is_team:
        if team is None:
            raise DomainError(_("Choose a team to register."), code="team_required")
        roster = _roster_for_team(tournament, team, user)
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
        raise AlreadyRegistered() from exc
    RegistrationMember.objects.bulk_create(
        RegistrationMember(registration=registration, user=u) for u in roster
    )
    if is_free:
        _notify_confirmed(registration)
    return registration


def _notify_confirmed(registration: Registration) -> None:
    users = [member.user for member in registration.members.select_related("user")]
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
    user: User, tournament: Tournament, *, team: Team | None = None, payment_method: str | None = None
) -> RegistrationResult:
    """Register ``user`` (solo) or ``team`` (captain only). Paid tournaments require a payment method."""
    if not tournament.is_free and payment_method not in PaymentMethod.values:
        raise PaymentMethodRequired()

    description = _("Entry fee for %(tournament)s") % {"tournament": tournament.title}
    if tournament.is_free or payment_method == PaymentMethod.WALLET:
        with transaction.atomic():
            registration = _create_registration(user, tournament, team)
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

    registration = _create_registration(user, tournament, team)
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


@transaction.atomic
def withdraw(user: User, tournament: Tournament) -> Registration:
    """Leave a tournament before it starts. Paid entry fees are refunded to the wallet."""
    registration = (
        Registration.objects.active()
        .select_for_update()
        .filter(tournament=tournament, members__user=user)
        .select_related("tournament")
        .first()
    )
    if registration is None:
        raise DomainError(_("You are not registered in this tournament."), code="not_registered")
    if registration.registered_by_id != user.pk:
        raise NotAllowedError(_("Only the player who registered can withdraw."), code="not_registrant")
    if tournament.state != Tournament.State.SCHEDULED:
        raise DomainError(_("You can no longer withdraw from this tournament."), code="withdraw_closed")

    _refund_entry(registration, registration.entry_fee_paid, _("Tournament withdrawal refund"))
    registration.status = Registration.Status.WITHDRAWN
    registration.save(update_fields=["status", "updated_at"])
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
    """Teams ``user`` captains that could be registered in ``tournament``."""
    already = (
        Registration.objects.active().filter(tournament=tournament, team__isnull=False).values("team_id")
    )
    return (
        Team.objects.active()
        .filter(
            game=tournament.game,
            memberships__user=user,
            memberships__role=TeamMembership.Role.CAPTAIN,
        )
        .exclude(pk__in=already)
        .select_related("game")
    )
