from urllib.parse import parse_qs, urlparse

from django.urls import reverse

import pytest
import responses

from apps.accounts.tests.factories import GameAccountFactory
from apps.catalog.tests.factories import ProductFactory
from apps.notifications.models import Notification
from apps.orders import services
from apps.orders.models import CartItem, Order
from apps.payments.models import Payment, WalletTransaction
from apps.payments.services import credit, get_wallet

pytestmark = pytest.mark.django_db

CHECKOUT_URL = reverse("checkout")
ZARINPAL = "https://sandbox.zarinpal.com/pg/v4/payment"


def fund(user, amount):
    credit(user, amount, kind=WalletTransaction.Kind.TOPUP)


def mock_zarinpal_request(authority="A00000000000000000000000000000123456"):
    responses.post(
        f"{ZARINPAL}/request.json", json={"data": {"code": 100, "authority": authority}, "errors": []}
    )
    return authority


def mock_zarinpal_verify(code=100):
    responses.post(
        f"{ZARINPAL}/verify.json",
        json={"data": {"code": code, "ref_id": 201, "card_pan": "502229******5995"}, "errors": []},
    )


class TestWalletCheckout:
    def test_successful_wallet_checkout(self, auth_client, user):
        product = ProductFactory(price=100_000, stock=5)
        services.add_to_cart(user, product, quantity=2)
        fund(user, 250_000)

        response = auth_client.post(CHECKOUT_URL, {"paymentMethod": "wallet"}, format="json")

        assert response.status_code == 201
        body = response.json()
        assert body["paymentUrl"] is None
        assert body["order"]["status"] == "paid"
        assert body["order"]["total"] == 200_000
        assert get_wallet(user).balance == 50_000
        product.refresh_from_db()
        assert (product.stock, product.popularity) == (3, 2)
        assert not CartItem.objects.exists()
        assert Notification.objects.filter(user=user, kind="order").exists()

    def test_insufficient_balance_rolls_back(self, auth_client, user):
        services.add_to_cart(user, ProductFactory(price=100_000))
        fund(user, 10_000)

        response = auth_client.post(CHECKOUT_URL, {"paymentMethod": "wallet"}, format="json")

        assert response.status_code == 402
        assert response.json()["code"] == "insufficient_balance"
        assert not Order.objects.exists()
        assert get_wallet(user).balance == 10_000
        assert CartItem.objects.count() == 1

    def test_empty_cart(self, auth_client):
        response = auth_client.post(CHECKOUT_URL, {"paymentMethod": "wallet"}, format="json")

        assert response.json()["code"] == "empty_cart"

    def test_game_account_required_for_account_topups(self, auth_client, user):
        services.add_to_cart(user, ProductFactory(delivery_type="account"))
        fund(user, 1_000_000)

        response = auth_client.post(CHECKOUT_URL, {"paymentMethod": "wallet"}, format="json")

        assert response.json()["code"] == "game_account_required"

    def test_game_account_is_snapshotted(self, auth_client, user):
        services.add_to_cart(user, ProductFactory(delivery_type="account"))
        account = GameAccountFactory(user=user, title="Main", username="pro_gamer_99")
        fund(user, 1_000_000)

        body = auth_client.post(
            CHECKOUT_URL, {"paymentMethod": "wallet", "gameAccount": account.pk}, format="json"
        ).json()

        assert (body["order"]["gameAccountTitle"], body["order"]["gameAccountUsername"]) == (
            "Main",
            "pro_gamer_99",
        )

    def test_someone_elses_game_account_rejected(self, auth_client, user):
        services.add_to_cart(user, ProductFactory(delivery_type="account"))
        foreign = GameAccountFactory()

        response = auth_client.post(
            CHECKOUT_URL, {"paymentMethod": "wallet", "gameAccount": foreign.pk}, format="json"
        )

        assert response.status_code == 400
        assert "gameAccount" in response.json()["errors"]


class TestGatewayCheckout:
    @responses.activate
    def test_redirects_to_gateway_and_verifies(self, auth_client, api_client, user):
        authority = mock_zarinpal_request()
        product = ProductFactory(price=300_000, stock=1)
        services.add_to_cart(user, product)

        body = auth_client.post(CHECKOUT_URL, {"paymentMethod": "gateway"}, format="json").json()

        assert body["paymentUrl"] == f"https://sandbox.zarinpal.com/pg/StartPay/{authority}"
        assert body["order"]["status"] == "pending_payment"
        assert CartItem.objects.exists()  # kept until the payment succeeds

        mock_zarinpal_verify()
        callback = api_client.get(reverse("payment-callback"), {"Authority": authority, "Status": "OK"})

        assert callback.status_code == 302
        query = parse_qs(urlparse(callback.url).query)
        assert query["status"] == ["paid"]
        order = Order.objects.get()
        assert order.status == Order.Status.PAID
        assert Payment.objects.get().ref_id == "201"
        assert not CartItem.objects.exists()
        product.refresh_from_db()
        assert product.stock == 0

    @responses.activate
    def test_callback_is_idempotent(self, auth_client, api_client, user):
        authority = mock_zarinpal_request()
        services.add_to_cart(user, ProductFactory(stock=10))
        auth_client.post(CHECKOUT_URL, {"paymentMethod": "gateway"}, format="json")
        mock_zarinpal_verify()

        api_client.get(reverse("payment-callback"), {"Authority": authority, "Status": "OK"})
        api_client.get(reverse("payment-callback"), {"Authority": authority, "Status": "OK"})

        verify_calls = [call for call in responses.calls if call.request.url.endswith("verify.json")]
        assert len(verify_calls) == 1

    @responses.activate
    def test_cancelled_payment_marks_order_failed(self, auth_client, api_client, user):
        authority = mock_zarinpal_request()
        services.add_to_cart(user, ProductFactory())
        auth_client.post(CHECKOUT_URL, {"paymentMethod": "gateway"}, format="json")

        callback = api_client.get(reverse("payment-callback"), {"Authority": authority, "Status": "NOK"})

        assert parse_qs(urlparse(callback.url).query)["status"] == ["cancelled"]
        assert Order.objects.get().status == Order.Status.FAILED
        assert CartItem.objects.exists()

    @responses.activate
    def test_failed_verification(self, auth_client, api_client, user):
        authority = mock_zarinpal_request()
        services.add_to_cart(user, ProductFactory())
        auth_client.post(CHECKOUT_URL, {"paymentMethod": "gateway"}, format="json")
        mock_zarinpal_verify(code=-51)

        api_client.get(reverse("payment-callback"), {"Authority": authority, "Status": "OK"})

        assert Payment.objects.get().status == Payment.Status.FAILED
        assert Order.objects.get().status == Order.Status.FAILED

    @responses.activate
    def test_stock_sold_out_before_verify_refunds_to_wallet(self, auth_client, api_client, user):
        authority = mock_zarinpal_request()
        product = ProductFactory(price=300_000, stock=1)
        services.add_to_cart(user, product)
        auth_client.post(CHECKOUT_URL, {"paymentMethod": "gateway"}, format="json")
        product.stock = 0
        product.save()
        mock_zarinpal_verify()

        api_client.get(reverse("payment-callback"), {"Authority": authority, "Status": "OK"})

        assert Order.objects.get().status == Order.Status.REFUNDED
        assert get_wallet(user).balance == 300_000

    @responses.activate
    def test_gateway_outage_returns_502_and_fails_order(self, auth_client, user):
        responses.post(f"{ZARINPAL}/request.json", json={"data": [], "errors": {"code": -9}})
        services.add_to_cart(user, ProductFactory())

        response = auth_client.post(CHECKOUT_URL, {"paymentMethod": "gateway"}, format="json")

        assert response.status_code == 502
        assert Order.objects.get().status == Order.Status.FAILED

    def test_unknown_authority_redirects_with_failure(self, api_client):
        callback = api_client.get(reverse("payment-callback"), {"Authority": "nope", "Status": "OK"})

        assert parse_qs(urlparse(callback.url).query)["status"] == ["failed"]


class TestOrders:
    def _paid_order(self, user):
        services.add_to_cart(user, ProductFactory(price=1000))
        fund(user, 1000)
        return services.checkout(user, payment_method="wallet").order

    def test_list_and_detail(self, auth_client, user):
        order = self._paid_order(user)

        assert auth_client.get(reverse("order-list")).json()["results"][0]["number"] == order.number
        detail = auth_client.get(reverse("order-detail", args=[order.number])).json()
        assert detail["items"][0]["deliveredCode"] is None

    def test_orders_are_private(self, client_for, user):
        from apps.accounts.tests.factories import UserFactory

        order = self._paid_order(user)

        assert client_for(UserFactory()).get(reverse("order-detail", args=[order.number])).status_code == 404

    def test_paid_order_cannot_be_cancelled(self, auth_client, user):
        order = self._paid_order(user)

        response = auth_client.post(reverse("order-cancel", args=[order.number]))

        assert response.json()["code"] == "order_not_cancellable"

    @responses.activate
    def test_cancel_pending_order(self, auth_client, user):
        mock_zarinpal_request()
        services.add_to_cart(user, ProductFactory())
        number = auth_client.post(CHECKOUT_URL, {"paymentMethod": "gateway"}, format="json").json()["order"][
            "number"
        ]

        assert auth_client.post(reverse("order-cancel", args=[number])).json()["status"] == "cancelled"

    def test_complete_order_reveals_codes(self, auth_client, user):
        order = self._paid_order(user)
        item = order.items.get()
        item.delivered_code = "AAAA-BBBB-CCCC"
        item.save()

        services.complete_order(order)

        detail = auth_client.get(reverse("order-detail", args=[order.number])).json()
        assert detail["status"] == "completed"
        assert detail["items"][0]["deliveredCode"] == "AAAA-BBBB-CCCC"
