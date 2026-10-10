from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import random_code
from apps.core.validators import IMAGE_VALIDATORS

INVITE_CODE_LENGTH = 8
MAX_TEAM_MEMBERS = 10


def new_invite_code() -> str:
    return random_code(INVITE_CODE_LENGTH)


class TeamQuerySet(models.QuerySet):
    def active(self):
        return self.filter(dissolved_at__isnull=True)


class Team(TimeStampedModel):
    """A standing group of players. Tournaments pick a lineup from its members at registration."""

    name = models.CharField(_("name"), max_length=40)
    logo = models.ImageField(upload_to="teams/", blank=True, validators=IMAGE_VALIDATORS)
    invite_code = models.CharField(max_length=16, unique=True, default=new_invite_code)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    dissolved_at = models.DateTimeField(null=True, blank=True)

    # Lifetime counters, maintained by the tournament results service.
    matches_played = models.PositiveIntegerField(default=0)
    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    points = models.PositiveIntegerField(default=0, db_index=True)

    objects = TeamQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"), condition=Q(dissolved_at__isnull=True), name="unique_active_team_name"
            )
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_active(self) -> bool:
        return self.dissolved_at is None

    @property
    def win_rate(self) -> float:
        return round(self.wins * 100 / self.matches_played, 1) if self.matches_played else 0.0


class TeamMembership(models.Model):
    class Role(models.TextChoices):
        CAPTAIN = "captain", _("Captain")
        PLAYER = "player", _("Player")
        SUBSTITUTE = "substitute", _("Substitute")

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships"
    )
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.PLAYER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(fields=["team", "user"], name="unique_team_member"),
            models.UniqueConstraint(
                fields=["team"], condition=Q(role="captain"), name="one_captain_per_team"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.team} ({self.role})"


class TeamInvitation(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACCEPTED = "accepted", _("Accepted")
        DECLINED = "declined", _("Declined")
        CANCELLED = "cancelled", _("Cancelled")

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invitations")
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_invitations"
    )
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "invited_user"],
                condition=Q(status="pending"),
                name="unique_pending_invitation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.team} → {self.invited_user} ({self.status})"
