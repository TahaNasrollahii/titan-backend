from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import DomainError

from . import services
from .models import Cart, CartItem, Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = [
        "title",
        "variant_label",
        "quantity",
        "unit_price",
        "line_total",
        "delivered_code",
        "delivered_at",
    ]
    readonly_fields = ["title", "variant_label", "quantity", "unit_price", "line_total", "delivered_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Staff back office: enter redeem codes on each line, then use "Mark as delivered"."""

    list_display = ["number", "user", "status", "total", "payment_method", "created_at"]
    list_filter = ["status", "payment_method"]
    search_fields = ["number", "user__phone", "user__username", "game_account_username"]
    readonly_fields = [
        "number",
        "user",
        "payment_method",
        "subtotal",
        "discount",
        "total",
        "game_account",
        "game_account_title",
        "game_account_username",
        "paid_at",
        "completed_at",
        "created_at",
    ]
    inlines = [OrderItemInline]
    actions = ["mark_processing", "mark_delivered"]

    @admin.action(description=_("Mark as processing"))
    def mark_processing(self, request, queryset):
        updated = queryset.filter(status=Order.Status.PAID).update(status=Order.Status.PROCESSING)
        self.message_user(request, _("%(count)d order(s) marked as processing.") % {"count": updated})

    @admin.action(description=_("Mark as delivered (completed)"))
    def mark_delivered(self, request, queryset):
        for order in queryset:
            try:
                services.complete_order(order)
            except DomainError as error:
                self.message_user(request, f"{order.number}: {error.detail}", level=messages.ERROR)

    def has_add_permission(self, request):
        return False


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    raw_id_fields = ["product", "variant"]


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ["user", "updated_at"]
    search_fields = ["user__phone", "user__username"]
    raw_id_fields = ["user"]
    inlines = [CartItemInline]
