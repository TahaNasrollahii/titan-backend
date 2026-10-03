"""Payment gateway adapters. Select one with the ``PAYMENT_GATEWAY`` setting."""

import logging
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.conf import settings

import requests

from apps.core.exceptions import ExternalServiceError

from .models import Payment

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaymentRequestResult:
    authority: str
    redirect_url: str
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentVerifyResult:
    success: bool
    ref_id: str = ""
    card_pan: str = ""
    raw: dict = field(default_factory=dict)


class BaseGateway:
    name: str

    def request(self, payment: Payment, *, callback_url: str, mobile: str = "") -> PaymentRequestResult:
        raise NotImplementedError

    def verify(self, payment: Payment) -> PaymentVerifyResult:
        raise NotImplementedError


class ZarinpalGateway(BaseGateway):
    """ZarinPal REST v4 (``/pg/v4/payment/{request,verify}.json``). Amounts are sent in Toman (IRT)."""

    name = "zarinpal"
    timeout_seconds = 15
    SUCCESS_CODE = 100
    ALREADY_VERIFIED_CODE = 101

    @property
    def base_url(self) -> str:
        host = "sandbox.zarinpal.com" if settings.ZARINPAL_SANDBOX else "payment.zarinpal.com"
        return f"https://{host}/pg"

    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self.base_url}/v4/payment/{endpoint}.json"
        try:
            response = requests.post(url, json=payload, timeout=self.timeout_seconds)
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.exception("ZarinPal %s call failed", endpoint)
            raise ExternalServiceError() from exc
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _data(body: dict) -> dict:
        data = body.get("data")
        return data if isinstance(data, dict) else {}

    def request(self, payment: Payment, *, callback_url: str, mobile: str = "") -> PaymentRequestResult:
        payload = {
            "merchant_id": settings.ZARINPAL_MERCHANT_ID,
            "amount": payment.amount,
            "currency": "IRT",
            "callback_url": callback_url,
            "description": payment.description or f"Titan payment #{payment.pk}",
            "metadata": {"mobile": mobile} if mobile else {},
        }
        body = self._post("request", payload)
        data = self._data(body)
        if data.get("code") != self.SUCCESS_CODE or not data.get("authority"):
            logger.error("ZarinPal request rejected for payment %s: %s", payment.pk, body.get("errors"))
            raise ExternalServiceError()
        authority = data["authority"]
        return PaymentRequestResult(
            authority=authority, redirect_url=f"{self.base_url}/StartPay/{authority}", raw=body
        )

    def verify(self, payment: Payment) -> PaymentVerifyResult:
        payload = {
            "merchant_id": settings.ZARINPAL_MERCHANT_ID,
            "amount": payment.amount,
            "authority": payment.authority,
        }
        body = self._post("verify", payload)
        data = self._data(body)
        success = data.get("code") in (self.SUCCESS_CODE, self.ALREADY_VERIFIED_CODE)
        return PaymentVerifyResult(
            success=success,
            ref_id=str(data.get("ref_id", "")),
            card_pan=str(data.get("card_pan", "")),
            raw=body,
        )


class FakeGateway(BaseGateway):
    """Development gateway: redirects straight back to the callback and always approves."""

    name = "fake"

    def request(self, payment: Payment, *, callback_url: str, mobile: str = "") -> PaymentRequestResult:
        authority = f"FAKE-{uuid.uuid4().hex}"
        redirect_url = f"{callback_url}?{urlencode({'Authority': authority, 'Status': 'OK'})}"
        return PaymentRequestResult(authority=authority, redirect_url=redirect_url)

    def verify(self, payment: Payment) -> PaymentVerifyResult:
        return PaymentVerifyResult(success=True, ref_id=f"FAKE-{payment.pk}", card_pan="6037-99**-****-0000")


GATEWAYS: dict[str, type[BaseGateway]] = {
    ZarinpalGateway.name: ZarinpalGateway,
    FakeGateway.name: FakeGateway,
}


def get_gateway(name: str | None = None) -> BaseGateway:
    return GATEWAYS[name or settings.PAYMENT_GATEWAY]()
