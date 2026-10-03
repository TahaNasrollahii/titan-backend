"""Smoke tests: every admin page renders against a fully seeded database."""

from io import StringIO

from django.contrib.admin.sites import site
from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse

import pytest

from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.payments.models import Wallet, WalletTransaction

pytestmark = pytest.mark.django_db

MODELS = sorted(site._registry, key=lambda model: model._meta.label)


def _url(model, view: str, *args) -> str:
    return reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_{view}", args=args)


@pytest.fixture
def seeded(tmp_path):
    call_command("seed", "--assets-dir", str(tmp_path), "--admin-password", "pw", stdout=StringIO())


@pytest.fixture
def staff_client(client, seeded):
    client.force_login(User.objects.get(phone="09000000000"))
    return client


def test_dashboard_renders(staff_client):
    response = staff_client.get(reverse("admin:index"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "tt-kpis" in body
    assert "Latest orders" in body


def test_every_registered_model_renders(staff_client):
    """Changelist (plain and searched), add and change views for every registered model."""
    request = RequestFactory().get("/admin/")
    request.user = User.objects.get(phone="09000000000")
    failures = []

    for model in MODELS:
        urls = [_url(model, "changelist"), _url(model, "changelist") + "?q=a"]
        if site._registry[model].has_add_permission(request):
            urls.append(_url(model, "add"))
        instance = model._default_manager.first()
        if instance is not None:
            urls.append(_url(model, "change", instance.pk))
        failures += [(url, status) for url in urls if (status := staff_client.get(url).status_code) != 200]

    assert failures == []


def test_wallet_adjustment_dialog_credits_and_ledgers(staff_client):
    wallet = Wallet.objects.exclude(user__is_staff=True).first()
    before = wallet.balance
    url = reverse("admin:payments_wallet_adjust_balance", args=[wallet.pk])

    response = staff_client.post(
        url,
        {"_form_submitted": "on", "direction": "credit", "amount": 5000, "description": "goodwill"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert response["HX-Redirect"] == reverse("admin:payments_wallet_change", args=[wallet.pk])
    wallet.refresh_from_db()
    assert wallet.balance == before + 5000
    assert wallet.transactions.latest("id").kind == WalletTransaction.Kind.ADJUSTMENT


def test_broadcast_notifies_every_active_player(staff_client):
    active = User.objects.filter(is_active=True).count()

    staff_client.post(
        reverse("admin:notifications_notification_broadcast"),
        {"_form_submitted": "on", "title": "Maintenance tonight", "body": "Back soon."},
    )

    assert Notification.objects.filter(title="Maintenance tonight").count() == active
