from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import Badge, Friendship, GameAccount, OTPCode, RankTier, User, UserBadge


class UserBadgeInline(admin.TabularInline):
    model = UserBadge
    extra = 0
    autocomplete_fields = ["badge"]


class GameAccountInline(admin.TabularInline):
    model = GameAccount
    extra = 0
    fields = ["title", "username", "game"]
    show_change_link = True


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["-date_joined"]
    list_display = ["phone", "username", "full_name", "level", "points", "is_staff", "date_joined"]
    list_filter = ["is_staff", "is_superuser", "is_active"]
    search_fields = ["phone", "username", "full_name", "email"]
    readonly_fields = ["last_login", "date_joined", "last_seen"]
    inlines = [UserBadgeInline, GameAccountInline]
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        (
            _("Profile"),
            {"fields": ("username", "full_name", "email", "avatar", "avatar_seed", "favorite_game")},
        ),
        (_("Progress"), {"fields": ("level", "xp", "points")}),
        (_("Presence"), {"fields": ("presence_status", "current_game", "last_seen")}),
        (
            _("Permissions"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("phone", "password1", "password2")}),)


@admin.register(GameAccount)
class GameAccountAdmin(admin.ModelAdmin):
    list_display = ["title", "username", "user", "game", "created_at"]
    search_fields = ["title", "username", "user__phone", "user__username"]
    raw_id_fields = ["user"]
    exclude = ["password"]

    def get_readonly_fields(self, request, obj=None):
        if request.user.has_perm("accounts.reveal_password"):
            return ["revealed_password"]
        return []

    @admin.display(description=_("password"))
    def revealed_password(self, obj: GameAccount) -> str:
        return obj.password or "—"


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ["from_user", "to_user", "status", "created_at"]
    list_filter = ["status"]
    raw_id_fields = ["from_user", "to_user"]


@admin.register(OTPCode)
class OTPCodeAdmin(admin.ModelAdmin):
    list_display = ["phone", "purpose", "attempts", "created_at", "expires_at", "used_at"]
    list_filter = ["purpose"]
    search_fields = ["phone"]
    readonly_fields = ["phone", "purpose", "code_hash", "attempts", "created_at", "expires_at", "used_at"]

    def has_add_permission(self, request):
        return False


@admin.register(RankTier)
class RankTierAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "min_points"]


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name", "slug"]
