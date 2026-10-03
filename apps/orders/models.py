from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product, ProductVariant
from apps.core.fields import EncryptedTextField
from apps.core.models import TimeStampedModel
from apps.payments.models import PaymentMethod

MAX_QUANTITY_PER_LINE = 20


class Cart(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart")

    def __str__(self) -> str:
        return f"Cart of {self.user}"


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="+")
    variant = models.ForeignKey(
        ProductVariant, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    quantity = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(MAX_QUANTITY_PER_LINE)]
    )

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["cart", "product", "variant"], name="unique_cart_line"),
            models.UniqueConstraint(
                fields=["cart", "product"],
                condition=models.Q(variant__isnull=True),
                name="unique_cart_line_novar",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.product}"

    @property
    def unit_price(self) -> int:
        return self.variant.price if self.variant else self.product.price

    @property
    def unit_original_price(self) -> int:
        source = self.variant if self.variant else self.product
        return source.original_price or source.price

    @property
    def line_total(self) -> int:
        return self.unit_price * self.quantity


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", _("Awaiting payment")
        PAID = "paid", _("Paid")
        PROCESSING = "processing", _("Processing")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")
        FAILED = "failed", _("Payment failed")
        REFUNDED = "refunded", _("Refunded")

    PAID_STATUSES = (Status.PAID, Status.PROCESSING, Status.COMPLETED)

    number = models.CharField(max_length=20, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT, db_index=True
    )
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices)

    subtotal = models.PositiveBigIntegerField(help_text=_("Sum of pre-discount prices."))
    discount = models.PositiveBigIntegerField(default=0)
    total = models.PositiveBigIntegerField()

    game_account = models.ForeignKey(
        "accounts.GameAccount", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders"
    )
    game_account_title = models.CharField(max_length=80, blank=True)
    game_account_username = models.CharField(max_length=150, blank=True)

    paid_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    staff_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self) -> str:
        return self.number

    @property
    def is_paid(self) -> bool:
        return self.status in self.PAID_STATUSES


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL, related_name="order_items")
    variant = models.ForeignKey(
        ProductVariant, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    title = models.CharField(max_length=150)
    variant_label = models.CharField(max_length=80, blank=True)
    image = models.ImageField(blank=True)
    delivery_type = models.CharField(max_length=10, choices=Product.DeliveryType.choices)
    unit_price = models.PositiveBigIntegerField()
    unit_original_price = models.PositiveBigIntegerField()
    quantity = models.PositiveSmallIntegerField()
    line_total = models.PositiveBigIntegerField()

    delivered_code = EncryptedTextField(blank=True, help_text=_("Redeem code(s) delivered to the customer."))
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.title}"
