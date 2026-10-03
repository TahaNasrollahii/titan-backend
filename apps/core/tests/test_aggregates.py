from django.urls import reverse

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.catalog.tests.factories import GameFactory, ProductFactory
from apps.content.models import Announcement
from apps.notifications.services import notify
from apps.payments.models import WalletTransaction
from apps.payments.services import credit
from apps.teams.tests.factories import team_with_members
from apps.tournaments.tests.factories import TournamentFactory, confirm_player

pytestmark = pytest.mark.django_db


class TestHome:
    def test_anonymous_home(self, api_client):
        GameFactory(slug="fortnite", is_featured=True)
        GameFactory(slug="hidden", is_featured=False)
        TournamentFactory(slug="hero", is_featured=True)
        TournamentFactory(slug="not-hero")
        Announcement.objects.create(title="Cup", text="soon")

        body = api_client.get(reverse("home")).json()

        assert [t["slug"] for t in body["heroTournaments"]] == ["hero"]
        assert [g["slug"] for g in body["categories"]] == ["fortnite"]
        assert body["announcements"][0]["title"] == "Cup"
        assert body["myStats"] is None

    def test_falls_back_to_soonest_tournaments(self, api_client):
        TournamentFactory(slug="any")

        assert [t["slug"] for t in api_client.get(reverse("home")).json()["heroTournaments"]] == ["any"]

    def test_authenticated_home_has_stats(self, auth_client):
        assert auth_client.get(reverse("home")).json()["myStats"]["matches"] == 0


class TestSearch:
    def test_search_across_resources(self, api_client):
        GameFactory(slug="valorant", title="ولورنت", title_en="Valorant")
        TournamentFactory(slug="valorant-cup", title="Valorant Cup", game=GameFactory(slug="valorant"))
        ProductFactory(slug="vp", title="Valorant Points")
        ProductFactory(slug="steam", title="Steam")

        body = api_client.get(reverse("search"), {"q": "valorant"}).json()

        assert [g["slug"] for g in body["games"]] == ["valorant"]
        assert [t["slug"] for t in body["tournaments"]] == ["valorant-cup"]
        assert [p["slug"] for p in body["products"]] == ["vp"]

    def test_empty_query_returns_popular(self, api_client):
        GameFactory(slug="featured", is_featured=True)
        ProductFactory.create_batch(7)

        body = api_client.get(reverse("search")).json()

        assert [g["slug"] for g in body["games"]] == ["featured"]
        assert len(body["products"]) == 5


class TestDashboard:
    def test_overview(self, auth_client, user):
        credit(user, 1_450_000, kind=WalletTransaction.Kind.TOPUP)
        confirm_player(TournamentFactory(), user)
        team_with_members(1).memberships.create(user=user)
        notify(user, title="hello")

        body = auth_client.get(reverse("dashboard")).json()

        assert body["walletBalance"] == 1_450_000
        assert body["tournamentsJoined"] == 1
        assert body["activeTeams"] == 1
        assert body["unreadNotifications"] == 1
        assert len(body["recentTournaments"]) == 1
        assert body["recentOrders"] == []

    def test_requires_auth(self, api_client):
        assert api_client.get(reverse("dashboard")).status_code == 401

    def test_others_data_not_counted(self, auth_client):
        confirm_player(TournamentFactory(), UserFactory())

        assert auth_client.get(reverse("dashboard")).json()["tournamentsJoined"] == 0
