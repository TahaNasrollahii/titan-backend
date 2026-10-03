from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Count, Q, URLField
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    RangeDateFilter,
    RangeNumericFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from apps.core.admin import DANGER, INFO, SUCCESS, WARNING, image_header, initials, toman
from apps.core.exceptions import DomainError

from .models import (
    Match,
    PlayerStats,
    Registration,
    RegistrationMember,
    Season,
    TeamStats,
    Tournament,
    TournamentPrize,
)
from .services import bracket, results

TOURNAMENT_STATUS_LABELS = {
    Tournament.Status.UPCOMING: INFO,
    Tournament.Status.REGISTRATION_OPEN: SUCCESS,
    Tournament.Status.REGISTRATION_CLOSED: WARNING,
    Tournament.Status.LIVE: DANGER,
}
REGISTRATION_STATUS_LABELS = {
    Registration.Status.PENDING_PAYMENT: WARNING,
    Registration.Status.CONFIRMED: SUCCESS,
    Registration.Status.CANCELLED: DANGER,
}
MATCH_STATUS_LABELS = {
    Match.Status.SCHEDULED: INFO,
    Match.Status.LIVE: DANGER,
    Match.Status.COMPLETED: SUCCESS,
}


def entrant_name(registration: Registration | None) -> str:
    return registration.display_name if registration else "—"


class MatchSidesMixin:
    @admin.display(description=_("side A"))
    def side_a(self, obj: Match) -> str:
        return entrant_name(obj.participant_a)

    @admin.display(description=_("side B"))
    def side_b(self, obj: Match) -> str:
        return entrant_name(obj.participant_b)


class ReadOnlyTabularInline(TabularInline):
    extra = 0
    can_delete = False
    tab = True
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ------------------------------------------------------------------ seasons
@admin.register(Season)
class SeasonAdmin(ModelAdmin):
    list_display = ["name", "number", "starts_at", "ends_at", "current"]
    ordering = ["-number"]
    search_fields = ["name"]

    @display(description=_("current"), label={True: SUCCESS}, ordering="is_current")
    def current(self, obj: Season):
        return (True, _("Current")) if obj.is_current else None


# ------------------------------------------------------------------ tournaments
class TournamentPrizeInline(TabularInline):
    model = TournamentPrize
    extra = 0
    tab = True
    fields = ["place", "label", "amount", "points"]


class RegistrationInline(ReadOnlyTabularInline):
    model = Registration
    fk_name = "tournament"
    per_page = 20
    fields = ["entrant", "status", "seed", "final_placement", "entry_fee_paid", "confirmed_at"]
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("team", "player")

    @admin.display(description=_("entrant"))
    def entrant(self, obj: Registration) -> str:
        return obj.display_name


class MatchInline(MatchSidesMixin, ReadOnlyTabularInline):
    model = Match
    fk_name = "tournament"
    per_page = 20
    fields = ["round", "position", "side_a", "score_a", "score_b", "side_b", "status", "scheduled_at"]
    readonly_fields = fields

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "participant_a__team", "participant_a__player", "participant_b__team", "participant_b__player"
            )
        )


@admin.register(Tournament)
class TournamentAdmin(ModelAdmin):
    list_display = [
        "tournament",
        "game",
        "participant_type",
        "status_label",
        "entrants",
        "prize",
        "starts_at",
        "is_featured",
    ]
    list_display_links = ["tournament"]
    list_editable = ["is_featured"]
    list_filter = [
        ("state", ChoicesDropdownFilter),
        ("game", RelatedDropdownFilter),
        ("season", RelatedDropdownFilter),
        ("participant_type", ChoicesDropdownFilter),
        ("is_featured", BooleanRadioFilter),
        ("entry_fee", RangeNumericFilter),
        ("starts_at", RangeDateFilter),
    ]
    list_filter_submit = True
    list_select_related = ["game"]
    search_fields = ["title", "slug"]
    prepopulated_fields = {"slug": ["title"]}
    autocomplete_fields = ["game", "season"]
    readonly_fields = ["state", "created_at", "updated_at"]
    date_hierarchy = "starts_at"
    warn_unsaved_form = True
    fieldsets = (
        (None, {"fields": ("title", "slug", ("game", "season"), "cover_image", "description")}),
        (
            _("Format"),
            {
                "classes": ["tab"],
                "fields": (
                    ("participant_type", "team_size"),
                    ("format", "format_label"),
                    ("best_of", "max_participants"),
                    "region",
                ),
            },
        ),
        (
            _("Schedule"),
            {
                "classes": ["tab"],
                "fields": (
                    ("registration_opens_at", "registration_closes_at"),
                    ("starts_at", "ends_at"),
                    "state",
                ),
            },
        ),
        (
            _("Fees & prizes"),
            {
                "classes": ["tab"],
                "fields": (("entry_fee", "entry_fee_original"), ("prize_pool", "prize_currency")),
            },
        ),
        (_("Rules"), {"classes": ["tab"], "fields": ("rules",)}),
        (
            _("Broadcast"),
            {
                "classes": ["tab"],
                "fields": ("is_featured", "stream_url", "viewer_count", ("created_at", "updated_at")),
            },
        ),
    )
    inlines = [TournamentPrizeInline, RegistrationInline, MatchInline]
    actions = ["generate_bracket"]
    actions_detail = ["start_tournament", "view_on_storefront"]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, URLField):
            kwargs.setdefault("assume_scheme", "https")
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_queryset(self, request):
        confirmed = Q(registrations__status=Registration.Status.CONFIRMED)
        return (
            super().get_queryset(request).annotate(confirmed_count=Count("registrations", filter=confirmed))
        )

    # ---- columns
    @display(description=_("tournament"), header=True, ordering="title")
    def tournament(self, obj: Tournament):
        return [
            obj.title,
            obj.format_label or obj.get_format_display(),
            initials(obj.title),
            image_header(obj.cover_image),
        ]

    @display(description=_("status"), label=TOURNAMENT_STATUS_LABELS, ordering="state")
    def status_label(self, obj: Tournament):
        status = obj.public_status
        return status, Tournament.Status(status).label

    @admin.display(description=_("entrants"), ordering="confirmed_count")
    def entrants(self, obj: Tournament) -> str:
        return f"{obj.confirmed_count}/{obj.max_participants}"

    @admin.display(description=_("prize pool"), ordering="prize_pool")
    def prize(self, obj: Tournament) -> str:
        if obj.prize_currency == "USD":
            return f"${obj.prize_pool:,}"
        return toman(obj.prize_pool)

    # ---- actions
    @action(description=_("Generate bracket and start"), icon="account_tree", permissions=["change"])
    def generate_bracket(self, request, queryset):
        for tournament in queryset:
            self._start(request, tournament)

    @action(
        description=_("Generate bracket & start"),
        icon="account_tree",
        variant=ActionVariant.PRIMARY,
        permissions=["change"],
    )
    def start_tournament(self, request, object_id):
        self._start(request, get_object_or_404(Tournament, pk=object_id))
        return redirect(reverse("admin:tournaments_tournament_change", args=[object_id]))

    @action(description=_("View on storefront"), icon="open_in_new")
    def view_on_storefront(self, request, object_id):
        tournament = get_object_or_404(Tournament, pk=object_id)
        return redirect(f"{settings.FRONTEND_URL}/tournaments/{tournament.slug}")

    def _start(self, request, tournament: Tournament) -> None:
        try:
            bracket.generate_bracket(tournament)
        except DomainError as error:
            self.message_user(request, f"{tournament}: {error.detail}", level=messages.ERROR)
        else:
            self.message_user(request, _("Bracket generated for %(t)s.") % {"t": tournament})


# ------------------------------------------------------------------ registrations
class RegistrationMemberInline(TabularInline):
    model = RegistrationMember
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Registration)
class RegistrationAdmin(ModelAdmin):
    list_display = [
        "__str__",
        "tournament",
        "status_label",
        "entry_fee_display",
        "seed",
        "final_placement",
        "created_at",
    ]
    list_filter = [
        ("status", ChoicesDropdownFilter),
        ("tournament", RelatedDropdownFilter),
        ("created_at", RangeDateFilter),
    ]
    list_filter_submit = True
    list_select_related = ["tournament", "team", "player"]
    search_fields = ["team__name", "player__username", "player__phone", "tournament__title"]
    autocomplete_fields = ["tournament", "team", "player", "registered_by"]
    raw_id_fields = ["payment"]
    readonly_fields = ["created_at", "updated_at", "confirmed_at"]
    fieldsets = (
        (None, {"fields": ("tournament", ("player", "team"), "registered_by", "status")}),
        (_("Payment"), {"classes": ["tab"], "fields": ("entry_fee_paid", "payment", "confirmed_at")}),
        (_("Result"), {"classes": ["tab"], "fields": ("seed", "final_placement")}),
        (_("Dates"), {"classes": ["tab"], "fields": ("created_at", "updated_at")}),
    )
    inlines = [RegistrationMemberInline]

    @display(description=_("status"), label=REGISTRATION_STATUS_LABELS, ordering="status")
    def status_label(self, obj: Registration):
        return obj.status, obj.get_status_display()

    @admin.display(description=_("fee paid"), ordering="entry_fee_paid")
    def entry_fee_display(self, obj: Registration) -> str:
        return toman(obj.entry_fee_paid) if obj.entry_fee_paid else _("Free")


# ------------------------------------------------------------------ matches
@admin.register(Match)
class MatchAdmin(MatchSidesMixin, ModelAdmin):
    """Entering both scores on an undecided match reports the result through the results service."""

    list_display = ["match", "side_a", "score", "side_b", "status_label", "scheduled_at"]
    list_display_links = ["match"]
    list_filter = [
        ("status", ChoicesDropdownFilter),
        ("tournament", RelatedDropdownFilter),
        ("scheduled_at", RangeDateFilter),
    ]
    list_filter_submit = True
    list_select_related = [
        "tournament",
        "participant_a__team",
        "participant_a__player",
        "participant_b__team",
        "participant_b__player",
    ]
    search_fields = ["tournament__title", "lobby_code"]
    fieldsets = (
        (None, {"fields": ("tournament", ("round", "position"), "status")}),
        (
            _("Result"),
            {
                "description": _("Entering both scores completes the match and advances the winner."),
                "fields": (("participant_a", "score_a"), ("participant_b", "score_b"), "winner"),
            },
        ),
        (_("Lobby"), {"fields": ("scheduled_at", ("lobby_code", "best_of"))}),
    )
    readonly_fields = [
        "tournament",
        "round",
        "position",
        "participant_a",
        "participant_b",
        "winner",
        "status",
    ]

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        scores_changed = {"score_a", "score_b"} & set(form.changed_data)
        editable = {name: form.cleaned_data[name] for name in ("scheduled_at", "lobby_code", "best_of")}
        try:
            results.update_match(obj, **editable)
            if scores_changed and obj.score_a is not None and obj.score_b is not None:
                results.report_result(obj, score_a=obj.score_a, score_b=obj.score_b)
        except DomainError as error:
            self.message_user(request, str(error.detail), level=messages.ERROR)

    @display(description=_("match"), header=True, ordering="round")
    def match(self, obj: Match):
        return [
            obj.tournament.title,
            _("Round %(round)s · match %(n)s") % {"round": obj.round, "n": obj.position + 1},
        ]

    @admin.display(description=_("score"))
    def score(self, obj: Match) -> str:
        if obj.score_a is None or obj.score_b is None:
            return "–"
        return f"{obj.score_a} : {obj.score_b}"

    @display(description=_("status"), label=MATCH_STATUS_LABELS, ordering="status")
    def status_label(self, obj: Match):
        return obj.status, obj.get_status_display()


# ------------------------------------------------------------------ leaderboards
class StatsAdmin(ModelAdmin):
    list_filter = [
        ("game", RelatedDropdownFilter),
        ("season", RelatedDropdownFilter),
        ("points", RangeNumericFilter),
    ]
    list_filter_submit = True
    ordering = ["-points", "-wins"]

    @admin.display(description=_("win rate"))
    def win_rate_display(self, obj) -> str:
        return f"{obj.win_rate}%"


@admin.register(PlayerStats)
class PlayerStatsAdmin(StatsAdmin):
    list_display = ["user", "game", "season", "matches", "wins", "losses", "win_rate_display", "points"]
    list_select_related = ["user", "game", "season"]
    search_fields = ["user__username", "user__phone"]
    autocomplete_fields = ["user", "game", "season"]


@admin.register(TeamStats)
class TeamStatsAdmin(StatsAdmin):
    list_display = ["team", "game", "season", "matches", "wins", "losses", "win_rate_display", "points"]
    list_select_related = ["team", "game", "season"]
    search_fields = ["team__name"]
    autocomplete_fields = ["team", "game", "season"]
