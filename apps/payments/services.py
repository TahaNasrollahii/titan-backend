from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.core.exceptions import DomainError

from . import registry
from .gateways import get_gateway
from .models import Payment, Wallet, WalletTransaction

WALLET_GATEWAY = "wallet"


class InsufficientBalance(DomainError):
    status_code = 402
    default_detail = _("Your wallet balance is not enough.")
    default_code = "insufficient_balance"


class PaymentNotFound(DomainError):
    status_code = 404
    default_detail = _("Payment not found.")
    default_code = "payment_not_found"


@dataclass(frozen=True)
class PaymentStart:
    payment: Payment
    payment_url: str


# ------------------------------------------------------------------ wallet
def get_wallet(user: User) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def _locked_wallet(user: User) -> Wallet:
    get_wallet(user)
    return Wallet.objects.select_for_update().get(user=user)


@transaction.atomic
def credit(
    user: User, amount: int, *, kind: str, description: str = "", reference: str = ""
) -> WalletTransaction:
    if amount <= 0:
        raise ValueError("Credit amount must be positive.")
    wallet = _locked_wallet(user)
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet,
        amount=amount,
        kind=kind,
        balance_after=wallet.balance,
        description=str(description),
        reference=reference,
    )


@transaction.atomic
def debit(
    user: User, amount: int, *, kind: str, description: str = "", reference: str = ""
) -> WalletTransaction:
    if amount <= 0:
        raise ValueError("Debit amount must be positive.")
    wallet = _locked_wallet(user)
    if wallet.balance < amount:
        raise InsufficientBalance()
    wallet.balance -= amount
    wallet.save(update_fields=["balance", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet,
        amount=-amount,
        kind=kind,
        balance_after=wallet.balance,
        description=str(description),
        reference=reference,
    )


# ------------------------------------------------------------------ payments
def _create_payment(
    user: User, *, amount: int, gateway: str, purpose: str, object_id, reference, description
):
    return Payment.objects.create(
        user=user,
        amount=amount,
        gateway=gateway,
        purpose=purpose,
        object_id=object_id,
        reference=reference,
        description=str(description)[:200],
    )


@transaction.atomic
def pay_with_wallet(
    user: User,
    *,
    amount: int,
    purpose: str,
    debit_kind: str,
    object_id: int | None = None,
    reference: str = "",
    description: str = "",
) -> Payment:
    """Settle a purchase from the wallet and immediately run the purpose's fulfilment handler."""
    payment = _create_payment(
        user,
        amount=amount,
        gateway=WALLET_GATEWAY,
        purpose=purpose,
        object_id=object_id,
        reference=reference,
        description=description,
    )
    debit(user, amount, kind=debit_kind, description=description, reference=f"payment:{payment.pk}")
    _mark_paid(payment, ref_id=f"WALLET-{payment.pk}")
    registry.handler_for(purpose).on_paid(payment)
    return payment


def start_gateway_payment(
    user: User,
    *,
    amount: int,
    purpose: str,
    object_id: int | None = None,
    reference: str = "",
    description: str = "",
) -> PaymentStart:
    """Register a payment with the online gateway and return the URL the user must be sent to."""
    gateway = get_gateway()
    payment = _create_payment(
        user,
        amount=amount,
        gateway=gateway.name,
        purpose=purpose,
        object_id=object_id,
        reference=reference,
        description=description,
    )
    result = gateway.request(payment, callback_url=settings.PAYMENT_CALLBACK_URL, mobile=user.phone)
    payment.authority = result.authority
    payment.gateway_response = {"request": result.raw}
    payment.save(update_fields=["authority", "gateway_response", "updated_at"])
    return PaymentStart(payment=payment, payment_url=result.redirect_url)


def _mark_paid(payment: Payment, *, ref_id: str, card_pan: str = "") -> None:
    payment.status = Payment.Status.PAID
    payment.ref_id = ref_id
    payment.card_pan = card_pan
    payment.paid_at = timezone.now()
    payment.save(update_fields=["status", "ref_id", "card_pan", "paid_at", "gateway_response", "updated_at"])


def _mark_unsuccessful(payment: Payment, status: str) -> None:
    payment.status = status
    payment.save(update_fields=["status", "gateway_response", "updated_at"])
    registry.handler_for(payment.purpose).on_failed(payment)


@transaction.atomic
def process_gateway_callback(authority: str, status: str) -> Payment:
    """Verify a gateway return. Idempotent: a payment that is already final is returned untouched."""
    payment = Payment.objects.select_for_update().filter(authority=authority).first()
    if payment is None:
        raise PaymentNotFound()
    if payment.is_final:
        return payment

    if status != "OK":
        _mark_unsuccessful(payment, Payment.Status.CANCELLED)
        return payment

    result = get_gateway(payment.gateway).verify(payment)
    payment.gateway_response = {**payment.gateway_response, "verify": result.raw}
    if not result.success:
        _mark_unsuccessful(payment, Payment.Status.FAILED)
        return payment

    _mark_paid(payment, ref_id=result.ref_id, card_pan=result.card_pan)
    registry.handler_for(payment.purpose).on_paid(payment)
    return payment


def frontend_result_url(payment: Payment | None, *, status: str | None = None) -> str:
    params = {"status": status or (payment.status if payment else Payment.Status.FAILED)}
    if payment is not None:
        params |= {"paymentId": payment.pk, "purpose": payment.purpose, "reference": payment.reference}
    return f"{settings.FRONTEND_URL}{settings.PAYMENT_RESULT_PATH}?{urlencode(params)}"


# ------------------------------------------------------------------ wallet top-up
def start_topup(user: User, amount: int) -> PaymentStart:
    return start_gateway_payment(
        user,
        amount=amount,
        purpose=Payment.Purpose.WALLET_TOPUP,
        reference=f"topup:{user.pk}",
        description=_("Titan wallet top-up"),
    )


def _fulfil_topup(payment: Payment) -> None:
    credit(
        payment.user,
        payment.amount,
        kind=WalletTransaction.Kind.TOPUP,
        description=_("Wallet top-up"),
        reference=f"payment:{payment.pk}",
    )


def register_payment_handlers() -> None:
    registry.register(Payment.Purpose.WALLET_TOPUP, registry.PurposeHandler(on_paid=_fulfil_topup))
