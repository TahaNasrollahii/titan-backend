from io import StringIO

from django.apps import apps
from django.core.management import call_command

import pytest

from apps.accounts.models import User
from apps.orders.models import Order
from apps.payments.services import get_wallet
from apps.tournaments.models import Match, Tournament

pytestmark = pytest.mark.django_db

DOMAIN_APPS = {
    "accounts",
    "catalog",
    "orders",
    "payments",
    "teams",
    "tournaments",
    "notifications",
    "content",
}


def row_counts() -> dict[str, int]:
    return {
        model._meta.label: model.objects.count()
        for model in apps.get_models()
        if model._meta.app_label in DOMAIN_APPS
    }


def run_seed(tmp_path):
    call_command("seed", "--assets-dir", str(tmp_path), "--admin-password", "pw", stdout=StringIO())


def test_seed_is_idempotent_and_consistent(tmp_path):
    run_seed(tmp_path)
    first = row_counts()
    run_seed(tmp_path)

    assert row_counts() == first

    demo = User.objects.get(phone="09123456789")
    assert demo.username == "TahaTitan"
    assert get_wallet(demo).balance == 1_450_000
    assert set(Order.objects.filter(user=demo).values_list("status", flat=True)) == {
        "completed",
        "processing",
    }
    assert User.objects.get(phone="09000000000").is_superuser

    season_cup = Tournament.objects.get(slug="valorant-season-cup")
    assert season_cup.state == Tournament.State.LIVE
    live_semi = season_cup.matches.get(round=2, position=0)
    assert live_semi.status == Match.Status.LIVE
    assert {live_semi.participant_a.team.name, live_semi.participant_b.team.name} == {
        "Iran Titans",
        "Dark Phoenix",
    }

    completed = Tournament.objects.filter(state=Tournament.State.COMPLETED)
    assert completed.count() == 3
    assert all(t.registrations.filter(final_placement=1).exists() for t in completed)


def test_flush_refused_outside_debug(tmp_path, settings):
    settings.DEBUG = False

    with pytest.raises(Exception, match="DEBUG"):
        call_command("seed", "--flush", "--assets-dir", str(tmp_path), stdout=StringIO())
