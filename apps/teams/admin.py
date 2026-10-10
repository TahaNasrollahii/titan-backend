from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeNumericFilter, RelatedDropdownFilter
from unfold.decorators import display

from apps.core.admin import DANGER, SUCCESS, WARNING, image_header, initials

from .models import MAX_TEAM_MEMBERS, Team, TeamInvitation, TeamMembership

INVITATION_LABELS = {
    TeamInvitation.Status.PENDING: WARNING,
    TeamInvitation.Status.ACCEPTED: SUCCESS,
    TeamInvitation.Status.DECLINED: DANGER,
}


class DissolvedFilter(admin.SimpleListFilter):
    title = _("status")
    parameter_name = "dissolved"

    def lookups(self, request, model_admin):
        return [("no", _("Active")), ("yes", _("Dissolved"))]

    def queryset(self, request, queryset):
        if self.value() == "no":
            return queryset.filter(dissolved_at__isnull=True)
        if self.value() == "yes":
            return queryset.filter(dissolved_at__isnull=False)
        return queryset


class TeamMembershipInline(TabularInline):
    model = TeamMembership
    extra = 0
    tab = True
    autocomplete_fields = ["user"]
    fields = ["user", "role", "joined_at"]
    readonly_fields = ["joined_at"]


class TeamInvitationInline(TabularInline):
    model = TeamInvitation
    fk_name = "team"
    extra = 0
    tab = True
    autocomplete_fields = ["invited_user", "invited_by"]
    fields = ["invited_user", "invited_by", "status", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(Team)
class TeamAdmin(ModelAdmin):
    list_display = ["team", "members", "record", "points", "status_label"]
    list_display_links = ["team"]
    list_filter = [DissolvedFilter, ("points", RangeNumericFilter)]
    list_filter_submit = True
    search_fields = ["name"]
    readonly_fields = ["invite_code", "created_by", "created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("name", "logo")}),
        (
            _("Record"),
            {"classes": ["tab"], "fields": (("matches_played", "wins", "losses"), "points")},
        ),
        (
            _("Administration"),
            {
                "classes": ["tab"],
                "fields": ("invite_code", "created_by", "dissolved_at", ("created_at", "updated_at")),
            },
        ),
    )
    inlines = [TeamMembershipInline, TeamInvitationInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(member_count=Count("memberships"))

    @display(description=_("team"), header=True, ordering="name")
    def team(self, obj: Team):
        return [obj.name, obj.invite_code, initials(obj.name), image_header(obj.logo)]

    @admin.display(description=_("members"), ordering="member_count")
    def members(self, obj: Team) -> str:
        return f"{obj.member_count}/{MAX_TEAM_MEMBERS}"

    @admin.display(description=_("W–L"))
    def record(self, obj: Team) -> str:
        return f"{obj.wins}–{obj.losses}"

    @display(description=_("status"), label={True: SUCCESS, False: DANGER}, ordering="dissolved_at")
    def status_label(self, obj: Team):
        return (True, _("Active")) if obj.is_active else (False, _("Dissolved"))


@admin.register(TeamInvitation)
class TeamInvitationAdmin(ModelAdmin):
    list_display = ["team", "invited_user", "invited_by", "status_label", "created_at"]
    list_filter = [("status", ChoicesDropdownFilter), ("team", RelatedDropdownFilter)]
    list_select_related = ["team", "invited_user", "invited_by"]
    search_fields = ["team__name", "invited_user__username", "invited_user__phone"]
    autocomplete_fields = ["team", "invited_user", "invited_by"]

    @display(description=_("status"), label=INVITATION_LABELS, ordering="status")
    def status_label(self, obj: TeamInvitation):
        return obj.status, obj.get_status_display()
