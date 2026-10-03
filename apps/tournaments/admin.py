from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

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


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ["name", "number", "starts_at", "ends_at", "is_current"]


class TournamentPrizeInline(admin.TabularInline):
    model = TournamentPrize
    extra = 0


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "game",
        "participant_type",
        "state",
        "starts_at",
        "max_participants",
        "is_featured",
    ]
    list_filter = ["state", "game", "participant_type", "season", "is_featured"]
    search_fields = ["title", "slug"]
    prepopulated_fields = {"slug": ["title"]}
    readonly_fields = ["state"]
    inlines = [TournamentPrizeInline]
    actions = ["generate_bracket"]

    @admin.action(description=_("Generate bracket and start"))
    def generate_bracket(self, request, queryset):
        for tournament in queryset:
            try:
                bracket.generate_bracket(tournament)
            except DomainError as error:
                self.message_user(request, f"{tournament}: {error.detail}", level=messages.ERROR)
            else:
                self.message_user(request, _("Bracket generated for %(t)s.") % {"t": tournament})


class RegistrationMemberInline(admin.TabularInline):
    model = RegistrationMember
    extra = 0
    raw_id_fields = ["user"]


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ["__str__", "tournament", "status", "seed", "final_placement", "created_at"]
    list_filter = ["status", "tournament"]
    search_fields = ["team__name", "player__username", "player__phone"]
    raw_id_fields = ["tournament", "team", "player", "registered_by", "payment"]
    inlines = [RegistrationMemberInline]


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    """Entering both scores on an undecided match reports the result through the results service."""

    list_display = [
        "__str__",
        "participant_a",
        "score_a",
        "score_b",
        "participant_b",
        "status",
        "scheduled_at",
    ]
    list_filter = ["status", "tournament"]
    fields = [
        "tournament",
        "round",
        "position",
        "participant_a",
        "participant_b",
        "score_a",
        "score_b",
        "winner",
        "status",
        "scheduled_at",
        "lobby_code",
        "best_of",
    ]
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


@admin.register(PlayerStats)
class PlayerStatsAdmin(admin.ModelAdmin):
    list_display = ["user", "game", "season", "matches", "wins", "losses", "points"]
    list_filter = ["game", "season"]
    search_fields = ["user__username"]
    raw_id_fields = ["user"]


@admin.register(TeamStats)
class TeamStatsAdmin(admin.ModelAdmin):
    list_display = ["team", "game", "season", "matches", "wins", "losses", "points"]
    list_filter = ["game", "season"]
    search_fields = ["team__name"]
