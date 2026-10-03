from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db.models import Count
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    RangeDateFilter,
    RangeNumericFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from apps.core.admin import DANGER, INFO, SUCCESS, WARNING, initials, toman

from .models import Badge, Friendship, GameAccount, OTPCode, RankTier, User, UserBadge
from .ranks import tier_for_points

PRESENCE_LABELS = {
    User.Presence.ONLINE: SUCCESS,
    User.Presence.IN_GAME: INFO,
    User.Presence.AWAY: WARNING,
}
FRIENDSHIP_LABELS = {
    Friendship.Status.PENDING: WARNING,
    Friendship.Status.ACCEPTED: SUCCESS,
    Friendship.Status.DECLINED: DANGER,
}


class UserBadgeInline(TabularInline):
    model = UserBadge
    extra = 0
    tab = True
    autocomplete_fields = ["badge"]
    readonly_fields = ["awarded_at"]


class GameAccountInline(TabularInline):
    model = GameAccount
    extra = 0
    tab = True
    fields = ["title", "username", "game"]
    show_change_link = True


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    ordering = ["-date_joined"]
    list_display = ["player", "phone", "rank", "level", "points", "presence_label", "role", "date_joined"]
    list_filter = [
        ("is_staff", BooleanRadioFilter),
        ("is_active", BooleanRadioFilter),
        ("presence_status", ChoicesDropdownFilter),
        ("favorite_game", RelatedDropdownFilter),
        ("points", RangeNumericFilter),
        ("date_joined", RangeDateFilter),
    ]
    list_filter_submit = True
    search_fields = ["phone", "username", "full_name", "email"]
    readonly_fields = ["last_login", "date_joined", "last_seen", "wallet_balance"]
    autocomplete_fields = ["favorite_game", "current_game"]
    inlines = [GameAccountInline, UserBadgeInline]
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        (
            _("Profile"),
            {
                "classes": ["tab"],
                "fields": ("username", "full_name", "email", "avatar", "avatar_seed", "favorite_game"),
            },
        ),
        (
            _("Progress"),
            {"classes": ["tab"], "fields": (("level", "xp", "points"), "wallet_balance")},
        ),
        (
            _("Presence"),
            {"classes": ["tab"], "fields": ("presence_status", "current_game", "last_seen")},
        ),
        (
            _("Permissions"),
            {
                "classes": ["tab"],
                "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
            },
        ),
        (_("Dates"), {"classes": ["tab"], "fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("phone", "password1", "password2")}),)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("wallet")

    @display(description=_("player"), header=True, ordering="username")
    def player(self, obj: User):
        avatar = {"path": obj.avatar.url} if obj.avatar else None
        return [obj.display_name, obj.full_name or obj.email, initials(obj.display_name), avatar]

    @display(description=_("rank"), label=True)
    def rank(self, obj: User):
        tier = tier_for_points(obj.points)
        return tier.name if tier else None

    @display(description=_("presence"), label=PRESENCE_LABELS)
    def presence_label(self, obj: User):
        return obj.presence, User.Presence(obj.presence).label

    @display(description=_("role"), label={"superuser": DANGER, "staff": INFO})
    def role(self, obj: User):
        if obj.is_superuser:
            return "superuser", _("Superuser")
        if obj.is_staff:
            return "staff", _("Staff")
        return None

    @admin.display(description=_("wallet"))
    def wallet_balance(self, obj: User):
        wallet = getattr(obj, "wallet", None)
        if wallet is None:
            return "—"
        url = reverse("admin:payments_wallet_change", args=[wallet.pk])
        return format_html('<a href="{}" class="text-primary-600">{}</a>', url, toman(wallet.balance))


@admin.register(GameAccount)
class GameAccountAdmin(ModelAdmin):
    """Passwords are encrypted at rest and only shown to staff holding ``reveal_password``."""

    list_display = ["title", "username", "user", "game", "created_at"]
    list_filter = [("game", RelatedDropdownFilter)]
    search_fields = ["title", "username", "user__phone", "user__username"]
    autocomplete_fields = ["user", "game"]
    list_select_related = ["user", "game"]
    exclude = ["password"]

    def get_readonly_fields(self, request, obj=None):
        if request.user.has_perm("accounts.reveal_password"):
            return ["revealed_password"]
        return []

    @admin.display(description=_("password"))
    def revealed_password(self, obj: GameAccount) -> str:
        return format_html('<code class="tt-code">{}</code>', obj.password) if obj.password else "—"


@admin.register(Friendship)
class FriendshipAdmin(ModelAdmin):
    list_display = ["from_user", "to_user", "status_label", "created_at"]
    list_filter = [("status", ChoicesDropdownFilter)]
    search_fields = ["from_user__username", "from_user__phone", "to_user__username", "to_user__phone"]
    autocomplete_fields = ["from_user", "to_user"]
    list_select_related = ["from_user", "to_user"]

    @display(description=_("status"), label=FRIENDSHIP_LABELS, ordering="status")
    def status_label(self, obj: Friendship):
        return obj.status, obj.get_status_display()


@admin.register(OTPCode)
class OTPCodeAdmin(ModelAdmin):
    list_display = ["phone", "purpose", "attempts", "state", "created_at", "expires_at"]
    list_filter = [("purpose", ChoicesDropdownFilter), ("created_at", RangeDateFilter)]
    list_filter_submit = True
    search_fields = ["phone"]
    readonly_fields = ["phone", "purpose", "code_hash", "attempts", "created_at", "expires_at", "used_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @display(description=_("state"), label={"used": SUCCESS, "expired": DANGER, "active": INFO})
    def state(self, obj: OTPCode):
        if obj.used_at:
            return "used", _("Used")
        if obj.expires_at < timezone.now():
            return "expired", _("Expired")
        return "active", _("Active")


@admin.register(RankTier)
class RankTierAdmin(ModelAdmin):
    list_display = ["name", "slug", "min_points", "swatch", "ornament"]
    ordering = ["min_points"]
    prepopulated_fields = {"slug": ["name"]}

    @admin.display(description=_("colours"))
    def swatch(self, obj: RankTier):
        return format_html(
            '<span class="tt-swatch" style="background: linear-gradient(90deg, {}, {})"></span>',
            obj.color_from,
            obj.color_to,
        )


@admin.register(Badge)
class BadgeAdmin(ModelAdmin):
    list_display = ["name", "slug", "icon", "awarded"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(award_count=Count("awards"))

    @admin.display(description=_("awarded"), ordering="award_count")
    def awarded(self, obj: Badge) -> int:
        return obj.award_count
