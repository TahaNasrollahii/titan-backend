"""External integrations and staff back-office actions."""

from django.contrib.admin.sites import site
from django.test import RequestFactory
from django.urls import reverse

import pytest
import requests
import responses

from apps.accounts.sms import KavenegarSMSBackend
from apps.accounts.tests.factories import GameAccountFactory, UserFactory
from apps.catalog.tests.factories import ProductFactory
from apps.core.exceptions import ExternalServiceError
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.payments.gateways import ZarinpalGateway
from apps.payments.models import Payment, WalletTransaction
from apps.payments.services import credit
from apps.tournaments.models import Match, Tournament
from apps.tournaments.services import bracket
from apps.tournaments.tests.factories import TournamentFactory, confirm_player

pytestmark = pytest.mark.django_db


class TestKavenegar:
    @responses.activate
    def test_sends_template_lookup(self, settings):
        settings.KAVENEGAR_API_KEY = "KEY"
        responses.post(
            "https://api.kavenegar.com/v1/KEY/verify/lookup.json", json={"return": {"status": 200}}
        )

        KavenegarSMSBackend().send_otp("09121234567", "12345")

        assert "receptor=09121234567" in responses.calls[0].request.body
        assert "token=12345" in responses.calls[0].request.body

    @responses.activate
    def test_failure_raises_external_error(self, settings):
        settings.KAVENEGAR_API_KEY = "KEY"
        responses.post("https://api.kavenegar.com/v1/KEY/verify/lookup.json", status=500)

        with pytest.raises(ExternalServiceError):
            KavenegarSMSBackend().send_otp("09121234567", "12345")


@responses.activate
def test_zarinpal_network_error_is_external_error(user):
    responses.post(
        "https://sandbox.zarinpal.com/pg/v4/payment/request.json", body=requests.ConnectionError("down")
    )
    payment = Payment.objects.create(user=user, amount=1000, gateway="zarinpal", purpose="wallet_topup")

    with pytest.raises(ExternalServiceError):
        ZarinpalGateway().request(payment, callback_url="http://cb")


def test_zarinpal_production_host(settings):
    settings.ZARINPAL_SANDBOX = False

    assert ZarinpalGateway().base_url == "https://payment.zarinpal.com/pg"


class TestAdmin:
    @pytest.fixture
    def admin_request(self):
        request = RequestFactory().post("/admin/")
        request.user = UserFactory(is_staff=True, is_superuser=True)
        request._messages = type("Messages", (), {"add": lambda *args, **kwargs: None})()
        return request

    def test_mark_order_delivered(self, admin_request, user):
        order_services.add_to_cart(user, ProductFactory(price=1000))
        credit(user, 1000, kind=WalletTransaction.Kind.TOPUP)
        order = order_services.checkout(user, payment_method="wallet").order
        admin = site._registry[Order]

        admin.mark_delivered(admin_request, Order.objects.filter(pk=order.pk))

        order.refresh_from_db()
        assert order.status == Order.Status.COMPLETED

    def test_generate_bracket_action(self, admin_request):
        tournament = TournamentFactory()
        for _ in range(2):
            confirm_player(tournament, UserFactory())

        site._registry[Tournament].generate_bracket(
            admin_request, Tournament.objects.filter(pk=tournament.pk)
        )

        assert tournament.matches.count() == 1

    def test_admin_score_entry_reports_result(self, client):
        admin = UserFactory(is_staff=True, is_superuser=True)
        client.force_login(admin)
        tournament = TournamentFactory()
        for _ in range(2):
            confirm_player(tournament, UserFactory())
        bracket.generate_bracket(tournament)
        match = tournament.matches.get()

        client.post(
            reverse("admin:tournaments_match_change", args=[match.pk]),
            {
                "score_a": 13,
                "score_b": 4,
                "scheduled_at_0": "",
                "scheduled_at_1": "",
                "lobby_code": "",
                "best_of": 1,
            },
        )

        match.refresh_from_db()
        assert match.status == Match.Status.COMPLETED

    def test_game_account_password_revealed_only_with_permission(self, client):
        account = GameAccountFactory(password="hunter2")
        url = reverse("admin:accounts_gameaccount_change", args=[account.pk])
        staff = UserFactory(is_staff=True)
        staff.user_permissions.add(*_perms("view_gameaccount"))
        client.force_login(staff)
        assert "hunter2" not in client.get(url).content.decode()

        staff.user_permissions.add(*_perms("reveal_password"))
        staff = type(staff).objects.get(pk=staff.pk)  # drop the permission cache
        client.force_login(staff)
        assert "hunter2" in client.get(url).content.decode()


def _perms(codename):
    from django.contrib.auth.models import Permission

    return Permission.objects.filter(codename=codename)
