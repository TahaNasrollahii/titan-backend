from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import percent_off
from apps.core.validators import IMAGE_VALIDATORS


class Currency(models.TextChoices):
    IRT = "IRT", _("Toman")
    USD = "USD", _("US dollar")


class Region(models.TextChoices):
    MIDDLE_EAST = "me", _("Middle East")
    EUROPE = "eu", _("Europe")
    IRAN = "ir", _("Iran")
    INTERNATIONAL = "intl", _("International")


class Season(models.Model):
    number = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=60)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_current"], condition=Q(is_current=True), name="one_current_season"
            )
        ]

    def __str__(self) -> str:
        return self.name


class Tournament(TimeStampedModel):
    class ParticipantType(models.TextChoices):
        SOLO = "solo", _("Solo")
        TEAM = "team", _("Team")

    class Format(models.TextChoices):
        SINGLE_ELIMINATION = "single_elimination", _("Single elimination")

    class State(models.TextChoices):
        """Lifecycle state. Before ``live`` the public status is derived from the registration dates."""

        SCHEDULED = "scheduled", _("Scheduled")
        LIVE = "live", _("Live")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")

    class Status(models.TextChoices):
        """Public status exposed by the API (see ``selectors.annotate_status``)."""

        UPCOMING = "upcoming", _("Coming soon")
        REGISTRATION_OPEN = "registration_open", _("Registration open")
        REGISTRATION_CLOSED = "registration_closed", _("Registration closed")
        LIVE = "live", _("Live")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")

    slug = models.SlugField(unique=True, max_length=120)
    title = models.CharField(_("title"), max_length=150)
    game = models.ForeignKey("catalog.Game", on_delete=models.PROTECT, related_name="tournaments")
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="tournaments")
    description = models.TextField(blank=True)
    cover_image = models.ImageField(upload_to="tournaments/", blank=True, validators=IMAGE_VALIDATORS)

    participant_type = models.CharField(max_length=4, choices=ParticipantType.choices)
    team_size = models.PositiveSmallIntegerField(default=1, help_text=_("Players per team; 1 for solo."))
    format = models.CharField(max_length=30, choices=Format.choices, default=Format.SINGLE_ELIMINATION)
    format_label = models.CharField(max_length=80, blank=True, help_text=_('e.g. "5v5 — knockout"'))
    best_of = models.PositiveSmallIntegerField(default=1)
    region = models.CharField(max_length=4, choices=Region.choices, default=Region.MIDDLE_EAST)
    max_participants = models.PositiveIntegerField()

    entry_fee = models.PositiveBigIntegerField(_("entry fee (toman)"), default=0)
    entry_fee_original = models.PositiveBigIntegerField(null=True, blank=True)
    prize_pool = models.PositiveBigIntegerField(default=0)
    prize_currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.IRT)
    rules = models.JSONField(default=list, blank=True)

    registration_opens_at = models.DateTimeField()
    registration_closes_at = models.DateTimeField()
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.SCHEDULED, db_index=True)

    is_featured = models.BooleanField(default=False)
    viewer_count = models.PositiveIntegerField(default=0)
    stream_url = models.URLField(blank=True)

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(registration_closes_at__gte=models.F("registration_opens_at")),
                name="registration_window_valid",
            ),
            models.CheckConstraint(
                condition=Q(ends_at__gte=models.F("starts_at")), name="tournament_dates_valid"
            ),
            models.CheckConstraint(condition=Q(max_participants__gte=2), name="tournament_min_participants"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_team(self) -> bool:
        return self.participant_type == self.ParticipantType.TEAM

    @property
    def is_free(self) -> bool:
        return self.entry_fee == 0

    @property
    def entry_discount_percent(self) -> int:
        return percent_off(self.entry_fee, self.entry_fee_original)

    @property
    def public_status(self) -> str:
        """Same rules as ``selectors.annotate_status`` for a single, already-loaded instance."""
        if self.state != self.State.SCHEDULED:
            return self.state
        now = timezone.now()
        if now < self.registration_opens_at:
            return self.Status.UPCOMING
        if now < self.registration_closes_at:
            return self.Status.REGISTRATION_OPEN
        return self.Status.REGISTRATION_CLOSED


class TournamentPrize(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="prizes")
    place = models.PositiveSmallIntegerField()
    label = models.CharField(max_length=60, blank=True)
    amount = models.PositiveBigIntegerField(default=0, help_text=_("In the tournament's prize currency."))
    points = models.PositiveIntegerField(
        default=0, help_text=_("Rank points for every player at this place.")
    )

    class Meta:
        ordering = ["place"]
        constraints = [models.UniqueConstraint(fields=["tournament", "place"], name="unique_prize_place")]

    def __str__(self) -> str:
        return f"{self.tournament} — #{self.place}"


class RegistrationQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status__in=Registration.ACTIVE_STATUSES)

    def active_for_team(self, team):
        return self.active().filter(
            team=team, tournament__state__in=[Tournament.State.SCHEDULED, Tournament.State.LIVE]
        )


class Registration(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", _("Awaiting payment")
        CONFIRMED = "confirmed", _("Confirmed")
        WITHDRAWN = "withdrawn", _("Withdrawn")
        CANCELLED = "cancelled", _("Cancelled")

    ACTIVE_STATUSES = (Status.PENDING_PAYMENT, Status.CONFIRMED)

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="registrations")
    team = models.ForeignKey(
        "teams.Team", null=True, blank=True, on_delete=models.PROTECT, related_name="registrations"
    )
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="registrations",
    )
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=20, choices=Status.choices, db_index=True)
    entry_fee_paid = models.PositiveBigIntegerField(default=0)
    payment = models.ForeignKey(
        "payments.Payment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    seed = models.PositiveIntegerField(null=True, blank=True)
    final_placement = models.PositiveIntegerField(null=True, blank=True)

    objects = RegistrationQuerySet.as_manager()

    class Meta:
        ordering = ["seed", "created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(team__isnull=False, player__isnull=True) | Q(team__isnull=True, player__isnull=False)
                ),
                name="registration_team_xor_player",
            ),
            models.UniqueConstraint(
                fields=["tournament", "team"],
                condition=Q(status__in=["pending_payment", "confirmed"]),
                name="unique_active_team_registration",
            ),
            models.UniqueConstraint(
                fields=["tournament", "player"],
                condition=Q(status__in=["pending_payment", "confirmed"]),
                name="unique_active_player_registration",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.display_name} @ {self.tournament}"

    @property
    def display_name(self) -> str:
        if self.team_id:
            return self.team.name
        return self.player.display_name

    @property
    def tag(self) -> str:
        if self.team_id:
            return self.team.tag
        return self.player.display_name[:2].upper()


class RegistrationMember(models.Model):
    """Roster snapshot: every player taking part through a registration (the player itself for solo)."""

    registration = models.ForeignKey(Registration, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tournament_entries"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["registration", "user"], name="unique_registration_member")
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.registration}"


class Match(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Waiting for participants")
        SCHEDULED = "scheduled", _("Scheduled")
        LIVE = "live", _("Live")
        COMPLETED = "completed", _("Completed")
        BYE = "bye", _("Bye")

    class Slot(models.TextChoices):
        A = "a", "A"
        B = "b", "B"

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="matches")
    round = models.PositiveSmallIntegerField(help_text=_("1 is the first round."))
    position = models.PositiveSmallIntegerField(help_text=_("0-based index inside the round."))
    participant_a = models.ForeignKey(
        Registration, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    participant_b = models.ForeignKey(
        Registration, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    score_a = models.PositiveSmallIntegerField(null=True, blank=True)
    score_b = models.PositiveSmallIntegerField(null=True, blank=True)
    winner = models.ForeignKey(
        Registration, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    best_of = models.PositiveSmallIntegerField(default=1)
    next_match = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="feeder_matches"
    )
    next_slot = models.CharField(max_length=1, choices=Slot.choices, blank=True)
    lobby_code = models.CharField(max_length=40, blank=True, help_text=_("Only visible to the participants."))
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["round", "position"]
        verbose_name_plural = _("matches")
        constraints = [
            models.UniqueConstraint(fields=["tournament", "round", "position"], name="unique_match_slot")
        ]

    def __str__(self) -> str:
        return f"{self.tournament} R{self.round}#{self.position + 1}"

    @property
    def participants(self) -> list[Registration]:
        return [p for p in (self.participant_a, self.participant_b) if p is not None]


class StatsBase(models.Model):
    game = models.ForeignKey("catalog.Game", on_delete=models.CASCADE, related_name="+")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="+")
    matches = models.PositiveIntegerField(default=0)
    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    points = models.PositiveIntegerField(default=0, db_index=True)
    tournaments_played = models.PositiveIntegerField(default=0)
    tournaments_won = models.PositiveIntegerField(default=0)
    earnings_irt = models.PositiveBigIntegerField(default=0)
    earnings_usd = models.PositiveBigIntegerField(default=0)

    class Meta:
        abstract = True

    @property
    def win_rate(self) -> float:
        return round(self.wins * 100 / self.matches, 1) if self.matches else 0.0


class PlayerStats(StatsBase):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_stats")

    class Meta:
        ordering = ["-points", "-wins"]
        verbose_name_plural = _("player stats")
        constraints = [models.UniqueConstraint(fields=["user", "game", "season"], name="unique_player_stats")]

    def __str__(self) -> str:
        return f"{self.user} / {self.game} / {self.season}"


class TeamStats(StatsBase):
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="game_stats")

    class Meta:
        ordering = ["-points", "-wins"]
        verbose_name_plural = _("team stats")
        constraints = [models.UniqueConstraint(fields=["team", "game", "season"], name="unique_team_stats")]

    def __str__(self) -> str:
        return f"{self.team} / {self.game} / {self.season}"
