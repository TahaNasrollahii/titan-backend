"""Shared admin helpers, plus Unfold-styled admins for third-party models (auth groups, JWT tokens)."""

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.models import Group
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.translation import gettext_lazy as _

from rest_framework_simplejwt.token_blacklist.admin import (
    BlacklistedTokenAdmin as JWTBlacklistedTokenAdmin,
)
from rest_framework_simplejwt.token_blacklist.admin import (
    OutstandingTokenAdmin as JWTOutstandingTokenAdmin,
)
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import RangeDateTimeFilter

# Unfold label colours for the status choices used across apps.
SUCCESS, INFO, WARNING, DANGER = "success", "info", "warning", "danger"


def toman(value: int | None) -> str:
    return f"{value or 0:,} T"


def initials(text: str) -> str:
    return (text or "?")[:2]


def image_header(field) -> dict | None:
    """Thumbnail spec for ``@display(header=True)`` columns."""
    return {"path": field.url, "squared": True} if field else None


def redirect_after_action(request: HttpRequest, url: str) -> HttpResponse:
    """Dialog actions are submitted with htmx, which needs a header to leave the modal."""
    if request.headers.get("HX-Request"):
        response = HttpResponse()
        response["HX-Redirect"] = url
        return response
    return HttpResponseRedirect(url)


# ------------------------------------------------------------------ third-party models
admin.site.unregister(Group)
admin.site.unregister(OutstandingToken)
admin.site.unregister(BlacklistedToken)


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
    list_display = ["name", "member_count"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(members=Count("user"))

    @admin.display(description=_("members"), ordering="members")
    def member_count(self, obj: Group) -> int:
        return obj.members


@admin.register(OutstandingToken)
class OutstandingTokenAdmin(JWTOutstandingTokenAdmin, ModelAdmin):
    list_display = ["jti", "user", "created_at", "expires_at"]
    list_filter = [("expires_at", RangeDateTimeFilter)]
    list_filter_submit = True
    search_fields = ["jti", "user__phone", "user__username"]
    ordering = ["-created_at"]


@admin.register(BlacklistedToken)
class BlacklistedTokenAdmin(JWTBlacklistedTokenAdmin, ModelAdmin):
    search_fields = ["token__jti", "token__user__phone", "token__user__username"]
    ordering = ["-blacklisted_at"]
