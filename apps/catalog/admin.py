from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.forms.models import BaseInlineFormSet
from django.shortcuts import get_object_or_404, redirect
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, StackedInline, TabularInline
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

from . import services
from .models import (
    Game,
    Platform,
    Product,
    ProductCategory,
    ProductImage,
    ProductVariant,
    Review,
    WishlistItem,
)

LOW_STOCK = 5


def stock_label(stock: int | None):
    """``(variant, text)`` pair for an Unfold label; ``None`` stock means unlimited."""
    if stock is None:
        return SUCCESS, _("Unlimited")
    if stock == 0:
        return DANGER, _("Out of stock")
    if stock <= LOW_STOCK:
        return WARNING, f"{stock:,}"
    return INFO, f"{stock:,}"


# ``stock_label`` returns the colour as the label key, so the mapping is the identity.
STOCK_LABELS = {variant: variant for variant in (SUCCESS, DANGER, WARNING, INFO)}


# ------------------------------------------------------------------ games & taxonomy
@admin.register(Game)
class GameAdmin(ModelAdmin):
    list_display = ["game", "kind_label", "products", "tournaments", "is_featured", "is_active", "sort_order"]
    list_display_links = ["game"]
    list_editable = ["is_featured", "is_active", "sort_order"]
    list_filter = [
        ("kind", ChoicesDropdownFilter),
        ("is_featured", BooleanRadioFilter),
        ("is_active", BooleanRadioFilter),
    ]
    search_fields = ["title", "title_en", "slug"]
    prepopulated_fields = {"slug": ["title_en"]}
    ordering = ["sort_order", "title_en"]
    fieldsets = (
        (None, {"fields": (("title", "title_en"), "slug", ("kind", "genre"), "description")}),
        (_("Visibility"), {"classes": ["tab"], "fields": ("is_featured", "is_active", "sort_order")}),
        (
            _("Artwork"),
            {
                "classes": ["tab"],
                "fields": ("cover_image", "logo_image", "background_image", "character_image", "icon_image"),
            },
        ),
        (_("Accent"), {"classes": ["tab"], "fields": ("accent_color", "accent_gradient")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                product_count=Count("products", distinct=True),
                tournament_count=Count("tournaments", distinct=True),
            )
        )

    @display(description=_("game"), header=True, ordering="title_en")
    def game(self, obj: Game):
        return [
            obj.title_en,
            obj.title,
            initials(obj.title_en),
            image_header(obj.icon_image or obj.cover_image),
        ]

    @display(description=_("kind"), label={Game.Kind.GAME: INFO, Game.Kind.SERVICE: WARNING}, ordering="kind")
    def kind_label(self, obj: Game):
        return obj.kind, obj.get_kind_display()

    @admin.display(description=_("products"), ordering="product_count")
    def products(self, obj: Game) -> int:
        return obj.product_count

    @admin.display(description=_("tournaments"), ordering="tournament_count")
    def tournaments(self, obj: Game) -> int:
        return obj.tournament_count


@admin.register(Platform)
class PlatformAdmin(ModelAdmin):
    list_display = ["name", "slug", "sort_order"]
    list_editable = ["sort_order"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(ProductCategory)
class ProductCategoryAdmin(ModelAdmin):
    list_display = ["name", "slug", "products", "sort_order"]
    list_editable = ["sort_order"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(product_count=Count("products"))

    @admin.display(description=_("products"), ordering="product_count")
    def products(self, obj: ProductCategory) -> int:
        return obj.product_count


# ------------------------------------------------------------------ products
class ProductVariantFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        kept = [
            form
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("price")
        ]
        if not kept and self.instance.price is None:
            raise ValidationError(_("Set a fixed price or add at least one option."))


class ProductVariantInline(TabularInline):
    model = ProductVariant
    formset = ProductVariantFormSet
    extra = 0
    tab = True
    verbose_name = _("option")
    verbose_name_plural = _("options (price points)")
    fields = ["label", "price", "original_price", "stock", "sort_order"]


class ProductImageInline(StackedInline):
    model = ProductImage
    extra = 0
    tab = True
    fields = [("image", "alt"), "sort_order"]


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ["product", "game", "price_range", "stock_status", "flags", "rating", "is_active"]
    list_display_links = ["product"]
    list_editable = ["is_active"]
    list_filter = [
        ("is_active", BooleanRadioFilter),
        ("category", RelatedDropdownFilter),
        ("game", RelatedDropdownFilter),
        ("delivery_type", ChoicesDropdownFilter),
        ("is_bestseller", BooleanRadioFilter),
        ("is_new", BooleanRadioFilter),
        ("price", RangeNumericFilter),
        ("created_at", RangeDateFilter),
    ]
    list_filter_submit = True
    list_select_related = ["game", "category"]
    search_fields = ["title", "slug", "vendor"]
    prepopulated_fields = {"slug": ["title"]}
    autocomplete_fields = ["game", "category", "platforms"]
    readonly_fields = ["has_variants", "price_max", "rating_avg", "review_count", "created_at", "updated_at"]
    inlines = [ProductVariantInline, ProductImageInline]
    warn_unsaved_form = True
    actions = ["activate", "deactivate", "toggle_bestseller"]
    actions_detail = ["view_on_storefront"]
    fieldsets = (
        (
            None,
            {"fields": ("title", "slug", "subtitle", ("game", "category"), ("vendor", "platforms"), "image")},
        ),
        (
            _("Pricing & stock"),
            {
                "classes": ["tab"],
                "description": _(
                    "Products with options take their price and stock from the Options tab; "
                    "fill these only for fixed-price products."
                ),
                "fields": (("price", "original_price"), "stock", ("has_variants", "price_max")),
            },
        ),
        (
            _("Delivery"),
            {"classes": ["tab"], "fields": ("delivery_type", "delivery_info")},
        ),
        (
            _("Content"),
            {"classes": ["tab"], "fields": ("description", "features", "specs", "tags")},
        ),
        (
            _("Merchandising"),
            {
                "classes": ["tab"],
                "fields": (
                    ("is_active", "is_bestseller", "is_new"),
                    "popularity",
                    ("rating_avg", "review_count"),
                    ("created_at", "updated_at"),
                ),
            },
        ),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # The product form may have re-saved a stale price/stock; options are the source of truth.
        services.sync_variant_summary(form.instance.pk)

    # ---- columns
    @display(description=_("product"), header=True, ordering="title")
    def product(self, obj: Product):
        return [obj.title, obj.category.name, initials(obj.title), image_header(obj.image)]

    @admin.display(description=_("price"), ordering="price")
    def price_range(self, obj: Product) -> str:
        if obj.price is None:
            return "—"
        if obj.has_variants and obj.price_max and obj.price_max != obj.price:
            return f"{toman(obj.price)} – {toman(obj.price_max)}"
        return toman(obj.price)

    @display(description=_("stock"), label=STOCK_LABELS, ordering="stock")
    def stock_status(self, obj: Product):
        return stock_label(obj.stock)

    @display(description=_("badges"), label=True)
    def flags(self, obj: Product):
        labels = []
        if obj.is_bestseller:
            labels.append(_("Best seller"))
        if obj.is_new:
            labels.append(_("New"))
        if obj.discount_percent:
            labels.append(f"-{obj.discount_percent}%")
        return labels or None

    @admin.display(description=_("rating"), ordering="rating_avg")
    def rating(self, obj: Product) -> str:
        if not obj.review_count:
            return "—"
        return format_html(
            "<span class='tt-stars'>★</span> {} <span class='tt-muted'>({})</span>",
            obj.rating_avg,
            obj.review_count,
        )

    # ---- actions
    @action(description=_("Activate selected products"), icon="visibility", permissions=["change"])
    def activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, _("%(count)d product(s) activated.") % {"count": updated})

    @action(description=_("Deactivate selected products"), icon="visibility_off", permissions=["change"])
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, _("%(count)d product(s) hidden from the store.") % {"count": updated})

    @action(description=_("Toggle best-seller badge"), icon="local_fire_department", permissions=["change"])
    def toggle_bestseller(self, request, queryset):
        for product in queryset:
            product.is_bestseller = not product.is_bestseller
            product.save(update_fields=["is_bestseller", "updated_at"])

    @action(description=_("View on storefront"), icon="open_in_new", variant=ActionVariant.PRIMARY)
    def view_on_storefront(self, request, object_id):
        product = get_object_or_404(Product, pk=object_id)
        return redirect(f"{settings.FRONTEND_URL}/product/{product.slug}")


# ------------------------------------------------------------------ reviews & wishlists
@admin.register(Review)
class ReviewAdmin(ModelAdmin):
    list_display = ["review", "product", "stars", "verified", "visibility", "created_at"]
    list_filter = [
        ("is_approved", BooleanRadioFilter),
        ("verified_purchase", BooleanRadioFilter),
        ("rating", RangeNumericFilter),
        ("created_at", RangeDateFilter),
    ]
    list_filter_submit = True
    list_select_related = ["product", "user"]
    search_fields = ["product__title", "user__username", "author_name", "author_email", "comment"]
    autocomplete_fields = ["product", "user"]
    readonly_fields = ["verified_purchase", "created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("product", "user", ("author_name", "author_email"))}),
        (_("Review"), {"fields": ("rating", "comment", ("is_approved", "verified_purchase"))}),
        (_("Dates"), {"classes": ["collapse"], "fields": ("created_at", "updated_at")}),
    )
    actions = ["approve", "reject"]
    actions_row = ["toggle_visibility"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        services.recalculate_rating(obj.product)

    def delete_model(self, request, obj):
        services.delete_review(obj)

    def delete_queryset(self, request, queryset):
        for review in queryset.select_related("product"):
            services.delete_review(review)

    @display(description=_("author"), header=True, ordering="author_name")
    def review(self, obj: Review):
        return [obj.author_name, obj.author_email, initials(obj.author_name)]

    @admin.display(description=_("rating"), ordering="rating")
    def stars(self, obj: Review):
        return format_html(
            '<span class="tt-stars" title="{}/5">{}<span class="tt-stars-off">{}</span></span>',
            obj.rating,
            "★" * obj.rating,
            "★" * (5 - obj.rating),
        )

    @display(description=_("purchase"), label={True: SUCCESS})
    def verified(self, obj: Review):
        return (True, _("Verified")) if obj.verified_purchase else None

    @display(description=_("status"), label={True: SUCCESS, False: DANGER}, ordering="is_approved")
    def visibility(self, obj: Review):
        return (True, _("Visible")) if obj.is_approved else (False, _("Hidden"))

    @action(description=_("Approve selected reviews"), icon="check_circle", permissions=["change"])
    def approve(self, request, queryset):
        self._set_approval(queryset, approved=True)

    @action(description=_("Hide selected reviews"), icon="block", permissions=["change"])
    def reject(self, request, queryset):
        self._set_approval(queryset, approved=False)

    @action(description=_("Show / hide"), icon="swap_horiz", permissions=["change"])
    def toggle_visibility(self, request, object_id):
        review = get_object_or_404(Review, pk=object_id)
        self._set_approval(Review.objects.filter(pk=review.pk), approved=not review.is_approved)
        return redirect(request.headers.get("referer") or "admin:catalog_review_changelist")

    @staticmethod
    def _set_approval(queryset, *, approved: bool) -> None:
        products = {review.product for review in queryset.select_related("product")}
        queryset.update(is_approved=approved)
        for product in products:
            services.recalculate_rating(product)


@admin.register(WishlistItem)
class WishlistItemAdmin(ModelAdmin):
    list_display = ["user", "product", "created_at"]
    list_filter = [("product", RelatedDropdownFilter)]
    list_select_related = ["user", "product"]
    search_fields = ["user__phone", "user__username", "product__title"]
    autocomplete_fields = ["user", "product"]
