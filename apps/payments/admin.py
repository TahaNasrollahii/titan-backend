from django.contrib import admin

from .models import Payment, Wallet, WalletTransaction


class WalletTransactionInline(admin.TabularInline):
    model = WalletTransaction
    extra = 0
    can_delete = False
    readonly_fields = ["amount", "kind", "balance_after", "description", "reference", "created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    """Balances are read-only here; use wallet services (credit/debit) so every change is ledgered."""

    list_display = ["user", "balance", "updated_at"]
    search_fields = ["user__phone", "user__username"]
    readonly_fields = ["user", "balance"]
    inlines = [WalletTransactionInline]

    def has_add_permission(self, request):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "purpose", "amount", "gateway", "status", "ref_id", "created_at"]
    list_filter = ["status", "purpose", "gateway"]
    search_fields = ["authority", "ref_id", "reference", "user__phone"]
    readonly_fields = [field.name for field in Payment._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
