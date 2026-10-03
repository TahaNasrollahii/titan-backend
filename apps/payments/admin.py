from django import forms
from django.contrib import admin, messages
from django.shortcuts import get_object_or_404
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
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminIntegerFieldWidget, UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

from apps.core.admin import DANGER, INFO, SUCCESS, WARNING, initials, redirect_after_action, toman

from . import services
from .models import Payment, Wallet, WalletTransaction

PAYMENT_STATUS_LABELS = {
    Payment.Status.INITIATED: WARNING,
    Payment.Status.PAID: SUCCESS,
    Payment.Status.FAILED: DANGER,
}
TRANSACTION_KIND_LABELS = {
    WalletTransaction.Kind.TOPUP: SUCCESS,
    WalletTransaction.Kind.REFUND: INFO,
    WalletTransaction.Kind.PRIZE: SUCCESS,
    WalletTransaction.Kind.ORDER_PAYMENT: WARNING,
    WalletTransaction.Kind.TOURNAMENT_FEE: WARNING,
}


def signed_toman(amount: int) -> str:
    return f"+{toman(amount)}" if amount > 0 else toman(amount)


class WalletTransactionInline(TabularInline):
    model = WalletTransaction
    extra = 0
    can_delete = False
    per_page = 20
    ordering = ["-created_at"]
    fields = ["created_at", "kind", "signed_amount", "balance_after", "description", "reference"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("amount"))
    def signed_amount(self, obj: WalletTransaction) -> str:
        return signed_toman(obj.amount)


class WalletAdjustmentForm(BaseDialogForm):
    DIRECTIONS = [("credit", _("Credit (add money)")), ("debit", _("Debit (remove money)"))]

    direction = forms.ChoiceField(label=_("Direction"), choices=DIRECTIONS, widget=UnfoldAdminSelectWidget)
    amount = forms.IntegerField(label=_("Amount (toman)"), min_value=1, widget=UnfoldAdminIntegerFieldWidget)
    description = forms.CharField(
        label=_("Reason"),
        max_length=200,
        help_text=_("Shown to the player in their wallet history."),
        widget=UnfoldAdminTextInputWidget,
    )


@admin.register(Wallet)
class WalletAdmin(ModelAdmin):
    """Balances are never edited directly; adjustments go through the wallet service so they are ledgered."""

    list_display = ["owner", "balance_display", "updated_at"]
    list_filter = [("balance", RangeNumericFilter)]
    list_filter_submit = True
    list_select_related = ["user"]
    search_fields = ["user__phone", "user__username"]
    readonly_fields = ["user", "balance_display", "created_at", "updated_at"]
    fields = ["user", "balance_display", ("created_at", "updated_at")]
    inlines = [WalletTransactionInline]
    actions_detail = ["adjust_balance"]

    def has_add_permission(self, request):
        return False

    @display(description=_("owner"), header=True, ordering="user__username")
    def owner(self, obj: Wallet):
        name = obj.user.display_name
        return [name, obj.user.phone, initials(name)]

    @admin.display(description=_("balance"), ordering="balance")
    def balance_display(self, obj: Wallet) -> str:
        return toman(obj.balance)

    @action(
        description=_("Adjust balance"),
        icon="currency_exchange",
        variant=ActionVariant.PRIMARY,
        permissions=["payments.change_wallet"],
        dialog={
            "title": _("Adjust wallet balance"),
            "description": _("Creates a manual adjustment entry in the wallet ledger."),
            "form_class": WalletAdjustmentForm,
            "form_submit_text": _("Apply adjustment"),
        },
    )
    def adjust_balance(self, request, form, object_id):
        wallet = get_object_or_404(Wallet.objects.select_related("user"), pk=object_id)
        data = form.cleaned_data
        apply = services.credit if data["direction"] == "credit" else services.debit
        try:
            apply(
                wallet.user,
                data["amount"],
                kind=WalletTransaction.Kind.ADJUSTMENT,
                description=data["description"],
                reference=f"admin:{request.user.pk}",
            )
        except services.InsufficientBalance as error:
            self.message_user(request, str(error.detail), level=messages.ERROR)
        else:
            self.message_user(request, _("Wallet adjusted."))
        return redirect_after_action(request, reverse("admin:payments_wallet_change", args=[object_id]))


@admin.register(WalletTransaction)
class WalletTransactionAdmin(ModelAdmin):
    """Read-only ledger across all wallets."""

    list_display = ["created_at", "wallet", "kind_label", "signed_amount", "balance_after", "description"]
    list_filter = [
        ("kind", ChoicesDropdownFilter),
        ("amount", RangeNumericFilter),
        ("created_at", RangeDateFilter),
    ]
    list_filter_submit = True
    list_select_related = ["wallet__user"]
    search_fields = ["wallet__user__phone", "wallet__user__username", "reference", "description"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("kind"), label=TRANSACTION_KIND_LABELS, ordering="kind")
    def kind_label(self, obj: WalletTransaction):
        return obj.kind, obj.get_kind_display()

    @admin.display(description=_("amount"), ordering="amount")
    def signed_amount(self, obj: WalletTransaction) -> str:
        return signed_toman(obj.amount)


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = [
        "id",
        "user",
        "purpose_label",
        "amount_display",
        "gateway",
        "status_label",
        "ref_id",
        "created_at",
    ]
    list_filter = [
        ("status", ChoicesDropdownFilter),
        ("purpose", ChoicesDropdownFilter),
        "gateway",
        ("amount", RangeNumericFilter),
        ("created_at", RangeDateFilter),
    ]
    list_filter_submit = True
    list_select_related = ["user"]
    search_fields = ["authority", "ref_id", "reference", "user__phone", "user__username"]
    date_hierarchy = "created_at"
    readonly_fields = [field.name for field in Payment._meta.fields]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    ("user", "status"),
                    ("amount", "purpose"),
                    ("reference", "object_id"),
                    "description",
                )
            },
        ),
        (
            _("Gateway"),
            {
                "classes": ["tab"],
                "fields": ("gateway", "authority", ("ref_id", "card_pan"), "gateway_response"),
            },
        ),
        (_("Timeline"), {"classes": ["tab"], "fields": ("created_at", "updated_at", "paid_at")}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("status"), label=PAYMENT_STATUS_LABELS, ordering="status")
    def status_label(self, obj: Payment):
        return obj.status, obj.get_status_display()

    @display(description=_("purpose"), label=True, ordering="purpose")
    def purpose_label(self, obj: Payment):
        return obj.get_purpose_display()

    @admin.display(description=_("amount"), ordering="amount")
    def amount_display(self, obj: Payment) -> str:
        return toman(obj.amount)
