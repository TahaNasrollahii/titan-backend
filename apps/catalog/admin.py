from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.utils.translation import gettext_lazy as _

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


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ["title_en", "title", "slug", "kind", "is_featured", "is_active", "sort_order"]
    list_editable = ["is_featured", "is_active", "sort_order"]
    list_filter = ["kind", "is_featured", "is_active"]
    search_fields = ["title", "title_en", "slug"]
    prepopulated_fields = {"slug": ["title_en"]}


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "sort_order"]
    list_editable = ["sort_order"]


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "sort_order"]
    list_editable = ["sort_order"]


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


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    formset = ProductVariantFormSet
    extra = 0


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["title", "game", "category", "price", "stock", "is_active", "is_bestseller", "popularity"]
    list_filter = ["is_active", "is_bestseller", "is_new", "category", "game", "delivery_type"]
    list_editable = ["is_active", "is_bestseller"]
    search_fields = ["title", "slug", "vendor"]
    prepopulated_fields = {"slug": ["title"]}
    filter_horizontal = ["platforms"]
    readonly_fields = ["has_variants", "price_max", "rating_avg", "review_count"]
    autocomplete_fields = ["game"]
    inlines = [ProductVariantInline, ProductImageInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # The product form may have re-saved a stale price/stock; options are the source of truth.
        services.sync_variant_summary(form.instance.pk)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = [
        "product",
        "author_name",
        "author_email",
        "rating",
        "verified_purchase",
        "is_approved",
        "created_at",
    ]
    list_filter = ["is_approved", "verified_purchase", "rating"]
    search_fields = ["product__title", "user__username", "author_name", "author_email", "comment"]
    raw_id_fields = ["product", "user"]
    actions = ["approve", "reject"]

    @admin.action(description="Approve selected reviews")
    def approve(self, request, queryset):
        self._set_approval(queryset, approved=True)

    @admin.action(description="Hide selected reviews")
    def reject(self, request, queryset):
        self._set_approval(queryset, approved=False)

    @staticmethod
    def _set_approval(queryset, *, approved: bool) -> None:
        products = {review.product for review in queryset.select_related("product")}
        queryset.update(is_approved=approved)
        for product in products:
            services.recalculate_rating(product)


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ["user", "product", "created_at"]
    raw_id_fields = ["user", "product"]
