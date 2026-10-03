from django.contrib import admin, messages
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateFilter,
    RangeNumericFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from apps.core.admin import DANGER, INFO, SUCCESS, WARNING, initials, toman
from apps.core.exceptions import DomainError

from . import services
from .models import Cart, CartItem, Order, OrderItem

ORDER_STATUS_LABELS = {
    Order.Status.PENDING_PAYMENT: WARNING,
    Order.Status.PAID: INFO,
    Order.Status.PROCESSING: INFO,
    Order.Status.COMPLETED: SUCCESS,
    Order.Status.FAILED: DANGER,
    Order.Status.REFUNDED: DANGER,
}


class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    verbose_name_plural = _("items — enter redeem codes here before marking the order delivered")
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
class OrderAdmin(ModelAdmin):
    """Staff back office: enter redeem codes on each line, then use "Mark as delivered"."""

    list_display = [
        "number",
        "customer",
        "status_label",
        "item_count",
        "amount",
        "payment_method",
        "created_at",
    ]
    list_filter = [
        ("status", ChoicesDropdownFilter),
        ("payment_method", ChoicesDropdownFilter),
        ("total", RangeNumericFilter),
        ("created_at", RangeDateFilter),
    ]
    list_filter_submit = True
    list_select_related = ["user"]
    search_fields = ["number", "user__phone", "user__username", "game_account_username"]
    date_hierarchy = "created_at"
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
        "updated_at",
    ]
    fieldsets = (
        (None, {"fields": (("number", "status"), "user", "staff_note")}),
        (_("Amounts"), {"classes": ["tab"], "fields": (("subtotal", "discount", "total"), "payment_method")}),
        (
            _("Game account"),
            {"classes": ["tab"], "fields": ("game_account", ("game_account_title", "game_account_username"))},
        ),
        (
            _("Timeline"),
            {"classes": ["tab"], "fields": (("created_at", "updated_at"), ("paid_at", "completed_at"))},
        ),
    )
    inlines = [OrderItemInline]
    actions = ["mark_processing", "mark_delivered"]
    actions_detail = ["start_processing", "deliver"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(line_count=Count("items"))

    def has_add_permission(self, request):
        return False

    # ---- columns
    @display(description=_("customer"), header=True, ordering="user__username")
    def customer(self, obj: Order):
        name = obj.user.display_name
        return [name, obj.user.phone, initials(name)]

    @display(description=_("status"), label=ORDER_STATUS_LABELS, ordering="status")
    def status_label(self, obj: Order):
        return obj.status, obj.get_status_display()

    @admin.display(description=_("items"), ordering="line_count")
    def item_count(self, obj: Order) -> int:
        return obj.line_count

    @admin.display(description=_("total"), ordering="total")
    def amount(self, obj: Order) -> str:
        return toman(obj.total)

    # ---- bulk actions
    @action(description=_("Mark as processing"), icon="pending_actions", permissions=["change"])
    def mark_processing(self, request, queryset):
        updated = queryset.filter(status=Order.Status.PAID).update(status=Order.Status.PROCESSING)
        self.message_user(request, _("%(count)d order(s) marked as processing.") % {"count": updated})

    @action(description=_("Mark as delivered (completed)"), icon="task_alt", permissions=["change"])
    def mark_delivered(self, request, queryset):
        for order in queryset:
            self._complete(request, order)

    # ---- detail actions
    @action(description=_("Start processing"), icon="pending_actions", permissions=["change"])
    def start_processing(self, request, object_id):
        updated = Order.objects.filter(pk=object_id, status=Order.Status.PAID).update(
            status=Order.Status.PROCESSING
        )
        if updated:
            self.message_user(request, _("Order marked as processing."))
        else:
            self.message_user(request, _("Only paid orders can move to processing."), level=messages.WARNING)
        return redirect(reverse("admin:orders_order_change", args=[object_id]))

    @action(
        description=_("Mark as delivered"),
        icon="task_alt",
        variant=ActionVariant.SUCCESS,
        permissions=["change"],
    )
    def deliver(self, request, object_id):
        self._complete(request, get_object_or_404(Order, pk=object_id))
        return redirect(reverse("admin:orders_order_change", args=[object_id]))

    def _complete(self, request, order: Order) -> None:
        try:
            services.complete_order(order)
        except DomainError as error:
            self.message_user(request, f"{order.number}: {error.detail}", level=messages.ERROR)
        else:
            self.message_user(request, _("%(number)s delivered.") % {"number": order.number})


class CartItemInline(TabularInline):
    model = CartItem
    extra = 0
    autocomplete_fields = ["product"]
    raw_id_fields = ["variant"]


@admin.register(Cart)
class CartAdmin(ModelAdmin):
    list_display = ["user", "lines", "quantity", "updated_at"]
    list_select_related = ["user"]
    search_fields = ["user__phone", "user__username"]
    autocomplete_fields = ["user"]
    inlines = [CartItemInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(line_count=Count("items"), units=Sum("items__quantity"))

    @admin.display(description=_("lines"), ordering="line_count")
    def lines(self, obj: Cart) -> int:
        return obj.line_count

    @admin.display(description=_("units"), ordering="units")
    def quantity(self, obj: Cart) -> int:
        return obj.units or 0
