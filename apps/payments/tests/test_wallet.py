from django.urls import reverse

import pytest
import responses

from apps.payments.models import Payment, WalletTransaction
from apps.payments.services import InsufficientBalance, credit, debit, get_wallet

pytestmark = pytest.mark.django_db

ZARINPAL = "https://sandbox.zarinpal.com/pg/v4/payment"


def test_credit_and_debit_are_ledgered(user):
    credit(user, 1000, kind=WalletTransaction.Kind.TOPUP)
    debit(user, 400, kind=WalletTransaction.Kind.ORDER_PAYMENT)

    assert get_wallet(user).balance == 600
    assert list(WalletTransaction.objects.order_by("id").values_list("amount", "balance_after")) == [
        (1000, 1000),
        (-400, 600),
    ]


def test_debit_cannot_overdraw(user):
    credit(user, 100, kind=WalletTransaction.Kind.TOPUP)

    with pytest.raises(InsufficientBalance):
        debit(user, 101, kind=WalletTransaction.Kind.ORDER_PAYMENT)
    assert get_wallet(user).balance == 100


@pytest.mark.parametrize("amount", [0, -5])
def test_non_positive_amounts_rejected(user, amount):
    with pytest.raises(ValueError):
        credit(user, amount, kind=WalletTransaction.Kind.TOPUP)


def test_wallet_endpoints(auth_client, user):
    credit(user, 5000, kind=WalletTransaction.Kind.TOPUP, description="seed")

    assert auth_client.get(reverse("wallet")).json()["balance"] == 5000
    transactions = auth_client.get(reverse("wallet-transactions")).json()["results"]
    assert [(t["amount"], t["kind"]) for t in transactions] == [(5000, "topup")]


@responses.activate
def test_topup_flow(auth_client, api_client, user):
    responses.post(
        f"{ZARINPAL}/request.json", json={"data": {"code": 100, "authority": "A123"}, "errors": []}
    )
    responses.post(f"{ZARINPAL}/verify.json", json={"data": {"code": 100, "ref_id": 9, "card_pan": "x"}})

    body = auth_client.post(reverse("wallet-topup"), {"amount": 200_000}, format="json").json()
    api_client.get(reverse("payment-callback"), {"Authority": "A123", "Status": "OK"})

    assert body["paymentUrl"].endswith("/StartPay/A123")
    assert get_wallet(user).balance == 200_000
    payment = auth_client.get(reverse("payment-detail", args=[body["paymentId"]])).json()
    assert payment["status"] == "paid"
    assert payment["purpose"] == "wallet_topup"


@responses.activate
def test_zarinpal_request_payload(auth_client, user):
    responses.post(f"{ZARINPAL}/request.json", json={"data": {"code": 100, "authority": "A1"}, "errors": []})

    auth_client.post(reverse("wallet-topup"), {"amount": 50_000}, format="json")

    sent = responses.calls[0].request
    import json

    payload = json.loads(sent.body)
    assert payload["amount"] == 50_000
    assert payload["currency"] == "IRT"
    assert payload["metadata"] == {"mobile": user.phone}


def test_topup_amount_limits(auth_client):
    assert auth_client.post(reverse("wallet-topup"), {"amount": 10}, format="json").status_code == 400


def test_payment_detail_is_private(auth_client, client_for):
    from apps.accounts.tests.factories import UserFactory

    other = UserFactory()
    payment = Payment.objects.create(user=other, amount=1, gateway="fake", purpose="wallet_topup")

    assert auth_client.get(reverse("payment-detail", args=[payment.pk])).status_code == 404


def test_fake_gateway_round_trip(settings, auth_client, api_client, user):
    settings.PAYMENT_GATEWAY = "fake"

    body = auth_client.post(reverse("wallet-topup"), {"amount": 100_000}, format="json").json()
    callback_path = body["paymentUrl"].split("localhost:8000", 1)[1]
    api_client.get(callback_path)

    assert get_wallet(user).balance == 100_000
