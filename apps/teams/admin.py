from django.contrib import admin

from .models import Team, TeamInvitation, TeamMembership


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 0
    raw_id_fields = ["user"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "tag", "game", "region", "points", "wins", "losses", "dissolved_at"]
    list_filter = ["game", "region"]
    search_fields = ["name", "tag"]
    readonly_fields = ["invite_code", "created_by"]
    inlines = [TeamMembershipInline]


@admin.register(TeamInvitation)
class TeamInvitationAdmin(admin.ModelAdmin):
    list_display = ["team", "invited_user", "invited_by", "status", "created_at"]
    list_filter = ["status"]
    raw_id_fields = ["team", "invited_user", "invited_by"]
