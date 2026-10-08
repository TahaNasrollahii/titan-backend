from dataclasses import dataclass
from datetime import date

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import GameAccount, User
from apps.catalog.models import Product, ProductVariant
from apps.core.exceptions import ConflictError, DomainError, ExternalServiceError
from apps.core.utils import random_code
from apps.notifications.services import Kind, notify
from apps.payments import registry
from apps.payments.models import Payment, PaymentMethod, WalletTransaction
from apps.payments.services import credit, pay_with_wallet, start_gateway_payment

from .models import MAX_QUANTITY_PER_LINE, Cart, CartItem, Order, OrderItem


# ------------------------------------------------------------------ errors
class ProductUnavailable(DomainError):
    default_detail = _("This product is not available.")
    default_code = "product_unavailable"


class OutOfStock(ConflictError):
    default_detail = _("Not enough stock for this product.")
    default_code = "out_of_stock"


class VariantRequired(DomainError):
    default_detail = _("Choose one of this product's options.")
    default_code = "variant_required"


class EmptyCart(DomainError):
    default_detail = _("Your cart is empty.")
    default_code = "empty_cart"


class GameAccountRequired(DomainError):
    default_detail = _("Select a game account to receive this order.")
    default_code = "game_account_required"


# ------------------------------------------------------------------ cart
@dataclass(frozen=True)
class CartSummary:
    cart: Cart
    items: list[CartItem]

    @property
    def count(self) -> int:
        return sum(item.quantity for item in self.items)

    @property
    def subtotal(self) -> int:
        return sum(item.unit_original_price * item.quantity for item in self.items)

    @property
    def total(self) -> int:
        return sum(item.line_total for item in self.items)

    @property
    def discount(self) -> int:
        return self.subtotal - self.total

    @property
    def requires_game_account(self) -> bool:
        return any(item.product.requires_game_account for item in self.items)


def get_cart(user: User) -> Cart:
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart


def cart_items(cart: Cart):
    return cart.items.select_related("product__game", "variant").order_by("created_at", "id")


def cart_summary(user: User) -> CartSummary:
    cart = get_cart(user)
    return CartSummary(cart=cart, items=list(cart_items(cart)))


def _available_stock(product: Product, variant: ProductVariant | None) -> int | None:
    """``None`` means unlimited."""
    return variant.stock if variant is not None else product.stock


def _validate_line(product: Product, variant: ProductVariant | None, quantity: int) -> None:
    if not product.is_purchasable:
        raise ProductUnavailable()
    if variant is not None and variant.product_id != product.pk:
        raise ProductUnavailable(_("This option does not belong to the product."), code="invalid_variant")
    if variant is None and product.has_variants:
        raise VariantRequired()
    stock = _available_stock(product, variant)
    if stock is not None and quantity > stock:
        raise OutOfStock()


@transaction.atomic
def add_to_cart(
    user: User, product: Product, variant: ProductVariant | None = None, quantity: int = 1
) -> CartItem:
    cart = get_cart(user)
    item = CartItem.objects.select_for_update().filter(cart=cart, product=product, variant=variant).first()
    new_quantity = min((item.quantity if item else 0) + quantity, MAX_QUANTITY_PER_LINE)
    _validate_line(product, variant, new_quantity)
    if item is None:
        return CartItem.objects.create(cart=cart, product=product, variant=variant, quantity=new_quantity)
    item.quantity = new_quantity
    item.save(update_fields=["quantity", "updated_at"])
    return item


@transaction.atomic
def update_cart_item(user: User, item_id: int, quantity: int) -> CartItem | None:
    """Set the quantity of a line. Zero removes it and returns ``None``."""
    item = (
        # Lock only the line: PostgreSQL refuses FOR UPDATE on the outer join to the nullable variant.
        CartItem.objects.select_for_update(of=("self",))
        .select_related("product", "variant")
        .get(cart__user=user, pk=item_id)
    )
    if quantity <= 0:
        item.delete()
        return None
    _validate_line(item.product, item.variant, quantity)
    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])
    return item


def remove_cart_item(user: User, item_id: int) -> None:
    CartItem.objects.filter(cart__user=user, pk=item_id).delete()


def clear_cart(user: User) -> None:
    CartItem.objects.filter(cart__user=user).delete()


def merge_guest_cart(user: User, lines: list[dict]) -> list[dict]:
    """Add a guest (browser-side) cart to the user's cart.

    Lines that are no longer purchasable are skipped and returned so the client can tell the user.
    """
    skipped = []
    for line in lines:
        try:
            add_to_cart(user, line["product"], line.get("variant"), line["quantity"])
        except DomainError as error:
            skipped.append({"product": line["product"].slug, "reason": error.code})
    return skipped


# ------------------------------------------------------------------ checkout
@dataclass(frozen=True)
class CheckoutResult:
    order: Order
    payment_url: str | None = None


def _new_order_number() -> str:
    return f"TTN-{date.today():%y%m%d}-{random_code(4)}"


def _snapshot_items(order: Order, items: list[CartItem]) -> None:
    OrderItem.objects.bulk_create(
        OrderItem(
            order=order,
            product=item.product,
            variant=item.variant,
            title=item.product.title,
            variant_label=item.variant.label if item.variant else "",
            image=item.product.image.name if item.product.image else "",
            delivery_type=item.product.delivery_type,
            unit_price=item.unit_price,
            unit_original_price=item.unit_original_price,
            quantity=item.quantity,
            line_total=item.line_total,
        )
        for item in items
    )


@transaction.atomic
def _place_order(user: User, payment_method: str, game_account: GameAccount | None) -> Order:
    summary = cart_summary(user)
    if not summary.items:
        raise EmptyCart()
    for item in summary.items:
        _validate_line(item.product, item.variant, item.quantity)
    if summary.requires_game_account and game_account is None:
        raise GameAccountRequired()
    if game_account is not None and game_account.user_id != user.pk:
        raise DomainError(_("Invalid game account."), code="invalid_game_account")

    for _attempt in range(5):
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    number=_new_order_number(),
                    user=user,
                    payment_method=payment_method,
                    subtotal=summary.subtotal,
                    discount=summary.discount,
                    total=summary.total,
                    game_account=game_account,
                    game_account_title=game_account.title if game_account else "",
                    game_account_username=game_account.username if game_account else "",
                )
            break
        except IntegrityError:  # order number collision — astronomically rare, just retry
            continue
    else:
        raise ConflictError(_("Could not create the order. Please try again."))
    _snapshot_items(order, summary.items)
    return order


def checkout(user: User, *, payment_method: str, game_account: GameAccount | None = None) -> CheckoutResult:
    """Turn the cart into an order and pay for it (wallet) or start an online payment (gateway)."""
    if payment_method == PaymentMethod.WALLET:
        with transaction.atomic():
            order = _place_order(user, payment_method, game_account)
            pay_with_wallet(
                user,
                amount=order.total,
                purpose=Payment.Purpose.ORDER,
                debit_kind=WalletTransaction.Kind.ORDER_PAYMENT,
                object_id=order.pk,
                reference=order.number,
                description=_("Payment for order %(number)s") % {"number": order.number},
            )
        order.refresh_from_db()
        return CheckoutResult(order=order)

    order = _place_order(user, payment_method, game_account)
    try:
        start = start_gateway_payment(
            user,
            amount=order.total,
            purpose=Payment.Purpose.ORDER,
            object_id=order.pk,
            reference=order.number,
            description=_("Payment for order %(number)s") % {"number": order.number},
        )
    except ExternalServiceError:
        Order.objects.filter(pk=order.pk).update(status=Order.Status.FAILED)
        raise
    return CheckoutResult(order=order, payment_url=start.payment_url)


# ------------------------------------------------------------------ payment handlers
class _StockShortage(Exception):
    pass


def _decrement_stock(item: OrderItem) -> None:
    if item.variant_id:
        owner = ProductVariant.objects.select_for_update().filter(pk=item.variant_id).first()
    else:
        owner = Product.objects.select_for_update().filter(pk=item.product_id).first()
    if owner is None or owner.stock is None:  # deleted product or unlimited stock
        return
    if owner.stock < item.quantity:
        raise _StockShortage
    owner.stock -= item.quantity
    owner.save(update_fields=["stock"])


def _reserve_stock(order: Order) -> bool:
    """Decrement stock for every line, all-or-nothing. Returns ``False`` if anything is short."""
    try:
        with transaction.atomic():
            for item in order.items.all():
                _decrement_stock(item)
    except _StockShortage:
        return False
    return True


def _refund_to_wallet(order: Order, payment: Payment, reason) -> None:
    credit(
        order.user,
        payment.amount,
        kind=WalletTransaction.Kind.REFUND,
        description=reason,
        reference=f"order:{order.number}",
    )
    order.status = Order.Status.REFUNDED
    order.save(update_fields=["status", "updated_at"])
    notify(
        order.user,
        kind=Kind.ORDER,
        title=_("Order refunded"),
        body=_("Order %(number)s could not be completed and was refunded to your wallet.")
        % {"number": order.number},
        icon="cart",
        data={"order_number": order.number},
    )


def _remove_ordered_lines_from_cart(order: Order) -> None:
    lines = Q()
    for item in order.items.all():
        lines |= Q(product_id=item.product_id, variant_id=item.variant_id)
    if lines:
        CartItem.objects.filter(lines, cart__user=order.user).delete()


def on_order_paid(payment: Payment) -> None:
    order = Order.objects.select_for_update().prefetch_related("items").get(pk=payment.object_id)
    if order.status != Order.Status.PENDING_PAYMENT:
        _refund_to_wallet(order, payment, _("Refund for an order that was no longer payable"))
        return
    if not _reserve_stock(order):
        _refund_to_wallet(order, payment, _("Refund: product went out of stock"))
        return

    order.status = Order.Status.PAID
    order.paid_at = timezone.now()
    order.save(update_fields=["status", "paid_at", "updated_at"])
    for item in order.items.all():
        if item.product_id:
            Product.objects.filter(pk=item.product_id).update(popularity=F("popularity") + item.quantity)
    _remove_ordered_lines_from_cart(order)
    notify(
        order.user,
        kind=Kind.ORDER,
        title=_("Order confirmed"),
        body=_("Order %(number)s was paid and is being processed.") % {"number": order.number},
        icon="cart",
        data={"order_number": order.number},
    )


def on_order_payment_failed(payment: Payment) -> None:
    Order.objects.filter(pk=payment.object_id, status=Order.Status.PENDING_PAYMENT).update(
        status=Order.Status.FAILED, updated_at=timezone.now()
    )


def register_payment_handlers() -> None:
    registry.register(
        Payment.Purpose.ORDER,
        registry.PurposeHandler(on_paid=on_order_paid, on_failed=on_order_payment_failed),
    )


# ------------------------------------------------------------------ lifecycle
def cancel_order(user: User, number: str) -> Order:
    order = Order.objects.get(user=user, number=number)
    if order.status not in (Order.Status.PENDING_PAYMENT, Order.Status.FAILED):
        raise DomainError(_("Only unpaid orders can be cancelled."), code="order_not_cancellable")
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    return order


@transaction.atomic
def complete_order(order: Order) -> Order:
    """Staff action: mark every line delivered and notify the customer."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.is_paid:
        raise DomainError(_("Only paid orders can be completed."), code="order_not_paid")
    now = timezone.now()
    order.items.filter(delivered_at__isnull=True).update(delivered_at=now)
    order.status = Order.Status.COMPLETED
    order.completed_at = now
    order.save(update_fields=["status", "completed_at", "updated_at"])
    notify(
        order.user,
        kind=Kind.ORDER,
        title=_("Order delivered"),
        body=_("Order %(number)s has been delivered.") % {"number": order.number},
        icon="check",
        data={"order_number": order.number},
    )
    return order


def user_orders(user: User):
    return Order.objects.filter(user=user).prefetch_related("items")
