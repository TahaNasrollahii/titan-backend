from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class PaymentMethod(models.TextChoices):
    """How a customer pays for something sold on Titan."""

    WALLET = "wallet", _("Wallet")
    GATEWAY = "gateway", _("Online gateway")


class Wallet(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.PositiveBigIntegerField(_("balance (toman)"), default=0)

    def __str__(self) -> str:
        return f"{self.user} — {self.balance:,}"


class WalletTransaction(models.Model):
    class Kind(models.TextChoices):
        TOPUP = "topup", _("Top-up")
        ORDER_PAYMENT = "order_payment", _("Order payment")
        TOURNAMENT_FEE = "tournament_fee", _("Tournament entry fee")
        REFUND = "refund", _("Refund")
        PRIZE = "prize", _("Prize")
        ADJUSTMENT = "adjustment", _("Manual adjustment")

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    amount = models.BigIntegerField(help_text=_("Positive for credit, negative for debit."))
    kind = models.CharField(max_length=20, choices=Kind.choices)
    balance_after = models.PositiveBigIntegerField()
    description = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=60, blank=True, help_text=_('e.g. "order:TTN-…"'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.kind} {self.amount:+,}"


class Payment(TimeStampedModel):
    class Purpose(models.TextChoices):
        ORDER = "order", _("Order")
        TOURNAMENT_REGISTRATION = "tournament_registration", _("Tournament registration")
        WALLET_TOPUP = "wallet_topup", _("Wallet top-up")

    class Status(models.TextChoices):
        INITIATED = "initiated", _("Initiated")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    FINAL_STATUSES = (Status.PAID, Status.FAILED, Status.CANCELLED)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    amount = models.PositiveBigIntegerField(_("amount (toman)"))
    gateway = models.CharField(max_length=20)
    purpose = models.CharField(max_length=30, choices=Purpose.choices)
    object_id = models.PositiveBigIntegerField(
        null=True, blank=True, help_text=_("Paid object's primary key.")
    )
    reference = models.CharField(max_length=60, blank=True, help_text=_("Human readable reference."))
    description = models.CharField(max_length=200, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.INITIATED, db_index=True)
    authority = models.CharField(max_length=64, unique=True, null=True, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    card_pan = models.CharField(max_length=32, blank=True)
    gateway_response = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["purpose", "object_id"])]

    def __str__(self) -> str:
        return f"#{self.pk} {self.purpose} {self.amount:,} ({self.status})"

    @property
    def is_final(self) -> bool:
        return self.status in self.FINAL_STATUSES
