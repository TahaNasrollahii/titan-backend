from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.core.exceptions import ConflictError, DomainError, NotAllowedError
from apps.notifications.services import Kind, notify, notify_many, resolve_action
from apps.tournaments.models import Registration

from .models import MAX_TEAM_MEMBERS, Team, TeamInvitation, TeamMembership, new_invite_code

Role = TeamMembership.Role


class TeamFull(ConflictError):
    default_detail = _("This team is full.")
    default_code = "team_full"


class AlreadyMember(ConflictError):
    default_detail = _("You are already a member of this team.")
    default_code = "already_member"


class NotCaptain(NotAllowedError):
    default_detail = _("Only the team captain can do this.")
    default_code = "not_captain"


# ------------------------------------------------------------------ queries
def teams_with_members() -> QuerySet[Team]:
    members = TeamMembership.objects.select_related("user", "user__current_game")
    return (
        Team.objects.active()
        .prefetch_related(Prefetch("memberships", queryset=members))
        .annotate(member_count=Count("memberships", distinct=True))
    )


def membership_of(team: Team, user: User) -> TeamMembership | None:
    if not user.is_authenticated:
        return None
    return team.memberships.filter(user=user).first()


def team_activity(team: Team) -> str:
    """Aggregate member presence for the sidebar rail: in_game > online > away > offline."""
    presences = {membership.user.presence for membership in team.memberships.all()}
    for state in (User.Presence.IN_GAME, User.Presence.ONLINE, User.Presence.AWAY):
        if state in presences:
            return state
    return User.Presence.OFFLINE


# ------------------------------------------------------------------ guards
def _require_captain(team: Team, user: User) -> None:
    if not team.memberships.filter(user=user, role=Role.CAPTAIN).exists():
        raise NotCaptain()


def _require_active(team: Team) -> None:
    if not team.is_active:
        raise DomainError(_("This team has been dissolved."), code="team_dissolved")


def _locked(team: Team) -> Team:
    return Team.objects.select_for_update().get(pk=team.pk)


def _add_member(team: Team, user: User, role: str = Role.PLAYER) -> TeamMembership:
    """Add ``user`` to a *locked* team, enforcing capacity and uniqueness."""
    _require_active(team)
    if team.memberships.filter(user=user).exists():
        raise AlreadyMember()
    if team.memberships.count() >= MAX_TEAM_MEMBERS:
        raise TeamFull()
    return TeamMembership.objects.create(team=team, user=user, role=role)


# ------------------------------------------------------------------ lifecycle
@transaction.atomic
def create_team(user: User, **fields) -> Team:
    try:
        with transaction.atomic():
            team = Team.objects.create(created_by=user, **fields)
    except IntegrityError as exc:
        raise ConflictError(_("A team with this name already exists."), code="team_name_taken") from exc
    TeamMembership.objects.create(team=team, user=user, role=Role.CAPTAIN)
    return team


@transaction.atomic
def update_team(team: Team, actor: User, **fields) -> Team:
    _require_active(team)
    _require_captain(team, actor)
    for name, value in fields.items():
        setattr(team, name, value)
    try:
        with transaction.atomic():
            team.save()
    except IntegrityError as exc:
        raise ConflictError(_("A team with this name already exists."), code="team_name_taken") from exc
    return team


@transaction.atomic
def dissolve_team(team: Team, actor: User) -> None:
    team = _locked(team)
    _require_active(team)
    _require_captain(team, actor)
    if Registration.objects.active_for_team(team).exists():
        raise DomainError(
            _("The team is registered in an ongoing tournament and cannot be dissolved."),
            code="team_in_tournament",
        )
    members = [membership.user for membership in team.memberships.select_related("user")]
    team.dissolved_at = timezone.now()
    team.save(update_fields=["dissolved_at", "updated_at"])
    team.invitations.filter(status=TeamInvitation.Status.PENDING).update(
        status=TeamInvitation.Status.CANCELLED
    )
    team.memberships.all().delete()
    others = [member for member in members if member.pk != actor.pk]
    notify_many(
        others,
        kind=Kind.TEAM,
        title=_("Team dissolved"),
        body=_("%(team)s was dissolved by its captain.") % {"team": team.name},
        icon="users",
    )


@transaction.atomic
def regenerate_invite_code(team: Team, actor: User) -> Team:
    _require_active(team)
    _require_captain(team, actor)
    team.invite_code = new_invite_code()
    team.save(update_fields=["invite_code", "updated_at"])
    return team


@transaction.atomic
def join_with_code(user: User, code: str) -> TeamMembership:
    team = Team.objects.active().select_for_update().filter(invite_code=code.strip().upper()).first()
    if team is None:
        raise DomainError(_("This invite link is invalid or has expired."), code="invalid_invite_code")
    membership = _add_member(team, user)
    TeamInvitation.objects.filter(team=team, invited_user=user, status=TeamInvitation.Status.PENDING).update(
        status=TeamInvitation.Status.ACCEPTED
    )
    return membership


# ------------------------------------------------------------------ roster management
@transaction.atomic
def remove_member(team: Team, actor: User, member: User) -> None:
    """Kick (captain) or leave (self). A captain must hand over captaincy before leaving."""
    team = _locked(team)
    _require_active(team)
    membership = team.memberships.filter(user=member).first()
    if membership is None:
        raise DomainError(_("This user is not a member of the team."), code="not_member")

    leaving = actor.pk == member.pk
    if not leaving:
        _require_captain(team, actor)
    if Registration.objects.active_for_team(team).filter(members__user=member).exists():
        raise DomainError(
            _("This player is in the team's lineup for an ongoing tournament."), code="member_in_lineup"
        )
    if membership.role == Role.CAPTAIN:
        if team.memberships.count() > 1:
            raise DomainError(
                _("Transfer captaincy to another member before leaving the team."),
                code="captain_must_transfer",
            )
        membership.delete()
        team.dissolved_at = timezone.now()
        team.save(update_fields=["dissolved_at", "updated_at"])
        return

    membership.delete()
    if not leaving:
        notify(
            member,
            kind=Kind.TEAM,
            title=_("Removed from team"),
            body=_("You were removed from %(team)s.") % {"team": team.name},
            icon="users",
        )


@transaction.atomic
def transfer_captaincy(team: Team, actor: User, new_captain: User) -> None:
    team = _locked(team)
    _require_active(team)
    _require_captain(team, actor)
    if actor.pk == new_captain.pk:
        return
    target = team.memberships.filter(user=new_captain).first()
    if target is None:
        raise DomainError(_("This user is not a member of the team."), code="not_member")
    team.memberships.filter(user=actor).update(role=Role.PLAYER)
    target.role = Role.CAPTAIN
    target.save(update_fields=["role"])
    notify(
        new_captain,
        kind=Kind.TEAM,
        title=_("You are now captain"),
        body=_("You are now the captain of %(team)s.") % {"team": team.name},
        icon="users",
    )


# ------------------------------------------------------------------ invitations
@transaction.atomic
def invite(team: Team, actor: User, invitee: User) -> TeamInvitation:
    _require_active(team)
    _require_captain(team, actor)
    if team.memberships.filter(user=invitee).exists():
        raise ConflictError(_("This player is already in the team."), code="already_member")
    try:
        with transaction.atomic():
            invitation = TeamInvitation.objects.create(team=team, invited_user=invitee, invited_by=actor)
    except IntegrityError as exc:
        raise ConflictError(
            _("This player already has a pending invitation."), code="invitation_pending"
        ) from exc
    notify(
        invitee,
        kind=Kind.TEAM_INVITE,
        title=_("Team invitation"),
        body=_("%(team)s invited you to join the team.") % {"team": team.name},
        icon="users",
        data={"invitation_id": invitation.pk, "team_id": team.pk, "team_name": team.name},
    )
    return invitation


def _pending_invitation(user: User, invitation_id: int) -> TeamInvitation:
    invitation = (
        TeamInvitation.objects.select_for_update()
        .select_related("team")
        .filter(pk=invitation_id, invited_user=user, status=TeamInvitation.Status.PENDING)
        .first()
    )
    if invitation is None:
        raise DomainError(_("Invitation not found."), code="not_found")
    return invitation


@transaction.atomic
def accept_invitation(user: User, invitation_id: int) -> TeamMembership:
    invitation = _pending_invitation(user, invitation_id)
    membership = _add_member(_locked(invitation.team), user)
    invitation.status = TeamInvitation.Status.ACCEPTED
    invitation.save(update_fields=["status", "updated_at"])
    resolve_action(user, kind=Kind.TEAM_INVITE, key="invitation_id", value=invitation.pk)
    notify(
        invitation.invited_by,
        kind=Kind.TEAM,
        title=_("Invitation accepted"),
        body=_("%(name)s joined %(team)s.") % {"name": user.display_name, "team": invitation.team.name},
        icon="users",
    )
    return membership


@transaction.atomic
def decline_invitation(user: User, invitation_id: int) -> TeamInvitation:
    invitation = _pending_invitation(user, invitation_id)
    invitation.status = TeamInvitation.Status.DECLINED
    invitation.save(update_fields=["status", "updated_at"])
    resolve_action(user, kind=Kind.TEAM_INVITE, key="invitation_id", value=invitation.pk)
    return invitation


@transaction.atomic
def cancel_invitation(team: Team, actor: User, invitation_id: int) -> None:
    _require_captain(team, actor)
    team.invitations.filter(pk=invitation_id, status=TeamInvitation.Status.PENDING).update(
        status=TeamInvitation.Status.CANCELLED
    )
