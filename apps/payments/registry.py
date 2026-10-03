"""Maps a payment purpose to the code that fulfils it.

Apps that sell something (orders, tournaments) register a handler from ``AppConfig.ready``,
so the payments app never imports them. Handlers run inside the payment's transaction.
"""

from collections.abc import Callable
from dataclasses import dataclass

from .models import Payment

PaymentCallback = Callable[[Payment], None]


def _noop(payment: Payment) -> None:
    return None


@dataclass(frozen=True)
class PurposeHandler:
    on_paid: PaymentCallback
    on_failed: PaymentCallback = _noop


_handlers: dict[str, PurposeHandler] = {}


def register(purpose: str, handler: PurposeHandler) -> None:
    _handlers[purpose] = handler


def handler_for(purpose: str) -> PurposeHandler:
    try:
        return _handlers[purpose]
    except KeyError as exc:
        raise LookupError(f"No payment handler registered for purpose {purpose!r}") from exc
