from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import BooleanRadioFilter, ChoicesDropdownFilter
from unfold.decorators import display

from apps.core.admin import DANGER, INFO, SEO_FIELDSET, SUCCESS, WARNING, image_header, initials, toman

from .models import Announcement, ContactChannel, Guide, GuideFaq, Promo, SiteSetting

WINDOW_LABELS = {"live": SUCCESS, "scheduled": INFO, "ended": DANGER, "off": WARNING}


def window_state(obj) -> tuple[str, str]:
    """Whether a time-boxed item is currently visible on the storefront."""
    now = timezone.now()
    if not obj.is_active:
        return "off", _("Disabled")
    if obj.starts_at and obj.starts_at > now:
        return "scheduled", _("Scheduled")
    if obj.ends_at and obj.ends_at < now:
        return "ended", _("Ended")
    return "live", _("Live")


@admin.register(Promo)
class PromoAdmin(ModelAdmin):
    list_display = [
        "promo",
        "placement_label",
        "price_display",
        "visibility",
        "ends_at",
        "is_active",
        "sort_order",
    ]
    list_display_links = ["promo"]
    list_editable = ["is_active", "sort_order"]
    list_filter = [("placement", ChoicesDropdownFilter), ("is_active", BooleanRadioFilter)]
    search_fields = ["title", "subtitle"]
    autocomplete_fields = ["product", "tournament"]
    ordering = ["placement", "sort_order"]
    fieldsets = (
        (None, {"fields": ("placement", "title", "subtitle", "badge")}),
        (
            _("Artwork"),
            {"classes": ["tab"], "fields": ("image", "background_image", "background_gradient", "layout")},
        ),
        (
            _("Offer"),
            {"classes": ["tab"], "fields": (("price", "original_price"), "discount_label")},
        ),
        (_("Target"), {"classes": ["tab"], "fields": ("product", "tournament", "link")}),
        (
            _("Schedule"),
            {"classes": ["tab"], "fields": (("starts_at", "ends_at"), ("is_active", "sort_order"))},
        ),
    )

    @display(description="Promo", header=True, ordering="title")
    def promo(self, obj: Promo):
        return [obj.title, obj.subtitle, initials(obj.title), image_header(obj.image or obj.background_image)]

    @display(description="Placement", label=True, ordering="placement")
    def placement_label(self, obj: Promo):
        return obj.get_placement_display()

    @admin.display(description="Price", ordering="price")
    def price_display(self, obj: Promo) -> str:
        return toman(obj.price) if obj.price else "—"

    @display(description="On storefront", label=WINDOW_LABELS)
    def visibility(self, obj: Promo):
        return window_state(obj)


@admin.register(Announcement)
class AnnouncementAdmin(ModelAdmin):
    list_display = ["title", "text", "visibility", "starts_at", "ends_at", "is_active", "sort_order"]
    list_editable = ["is_active", "sort_order"]
    list_filter = [("is_active", BooleanRadioFilter)]
    search_fields = ["title", "text"]
    fields = ["title", "text", ("icon", "link"), ("starts_at", "ends_at"), ("is_active", "sort_order")]

    @display(description="On storefront", label=WINDOW_LABELS)
    def visibility(self, obj: Announcement):
        return window_state(obj)


@admin.register(ContactChannel)
class ContactChannelAdmin(ModelAdmin):
    list_display = ["channel", "kind", "url", "is_primary", "is_online", "is_active", "sort_order"]
    list_display_links = ["channel"]
    list_editable = ["is_online", "is_active", "sort_order"]
    list_filter = [("kind", ChoicesDropdownFilter), ("is_active", BooleanRadioFilter)]
    search_fields = ["title", "url"]
    ordering = ["sort_order"]
    fields = [
        ("kind", "title"),
        "description",
        "url",
        ("icon", "action_label"),
        ("is_primary", "is_online", "is_active"),
        "sort_order",
    ]

    @display(description="Channel", header=True, ordering="title")
    def channel(self, obj: ContactChannel):
        return [obj.title, obj.description, initials(obj.title), image_header(obj.icon)]


@admin.register(SiteSetting)
class SiteSettingAdmin(ModelAdmin):
    list_display = ["key", "value", "description"]
    search_fields = ["key", "description"]


class GuideFaqInline(TabularInline):
    model = GuideFaq
    extra = 0
    fields = ["question", "answer", "sort_order"]


@admin.register(Guide)
class GuideAdmin(ModelAdmin):
    list_display = ["guide", "category_label", "game", "is_published", "sort_order", "updated_at"]
    list_display_links = ["guide"]
    list_editable = ["is_published", "sort_order"]
    list_filter = [
        ("category", ChoicesDropdownFilter),
        ("is_published", BooleanRadioFilter),
        "game",
    ]
    search_fields = ["title", "slug", "summary", "body"]
    autocomplete_fields = ["game", "products"]
    readonly_fields = ["published_at"]
    ordering = ["category", "sort_order"]
    inlines = [GuideFaqInline]
    fieldsets = (
        (None, {"fields": ("title", "slug", ("category", "game"), "summary", "body")}),
        (_("Links"), {"classes": ["tab"], "fields": ("products",)}),
        (
            _("Publishing"),
            {"classes": ["tab"], "fields": (("is_published", "published_at"), "sort_order")},
        ),
        SEO_FIELDSET,
    )

    @display(description="Guide", header=True, ordering="title")
    def guide(self, obj: Guide):
        return [obj.title, f"/guides/{obj.slug}", initials(obj.title), None]

    @display(description="Category", label=True, ordering="category")
    def category_label(self, obj: Guide):
        return obj.get_category_display()
