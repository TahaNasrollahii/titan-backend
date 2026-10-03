from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

import pytest
import responses

from apps.accounts.tests.factories import UserFactory
from apps.notifications.models import Notification
from apps.payments.models import WalletTransaction
from apps.payments.services import credit, get_wallet
from apps.teams.tests.factories import captain_of, team_with_members
from apps.tournaments.models import Registration, Tournament
from apps.tournaments.tests.factories import TournamentFactory, confirm_player, confirm_team

pytestmark = pytest.mark.django_db

ZARINPAL = "https://sandbox.zarinpal.com/pg/v4/payment"


def register_url(tournament):
    return reverse("tournament-register", args=[tournament.slug])


def fund(user, amount):
    credit(user, amount, kind=WalletTransaction.Kind.TOPUP)


class TestListing:
    def test_status_is_derived_from_dates_and_state(self, api_client):
        TournamentFactory(slug="open")
        TournamentFactory(slug="soon", upcoming=True)
        TournamentFactory(slug="closed", registration_closes_at=timezone.now() - timedelta(minutes=1))
        TournamentFactory(slug="live", state=Tournament.State.LIVE)

        statuses = {
            t["slug"]: t["status"] for t in api_client.get(reverse("tournament-list")).json()["results"]
        }

        assert statuses == {
            "open": "registration_open",
            "soon": "upcoming",
            "closed": "registration_closed",
            "live": "live",
        }

    def test_filter_by_status_and_game(self, api_client):
        TournamentFactory(slug="open")
        TournamentFactory(slug="soon", upcoming=True)

        response = api_client.get(reverse("tournament-list"), {"status": "upcoming"})

        assert [t["slug"] for t in response.json()["results"]] == ["soon"]

    def test_ordering_by_prize(self, api_client):
        TournamentFactory(slug="small", prize_pool=10)
        TournamentFactory(slug="big", prize_pool=1000)

        response = api_client.get(reverse("tournament-list"), {"ordering": "-prize_pool"})

        assert [t["slug"] for t in response.json()["results"]] == ["big", "small"]

    def test_full_flag_and_participant_count(self, api_client):
        tournament = TournamentFactory(max_participants=2)
        confirm_player(tournament, UserFactory())
        confirm_player(tournament, UserFactory())

        item = api_client.get(reverse("tournament-list")).json()["results"][0]

        assert (item["participantsCount"], item["isFull"]) == (2, True)

    def test_detail_with_my_registration(self, auth_client, user):
        tournament = TournamentFactory(entry_fee=50_000, entry_fee_original=150_000)
        confirm_player(tournament, user)

        body = auth_client.get(reverse("tournament-detail", args=[tournament.slug])).json()

        assert body["isRegistered"] is True
        assert body["myRegistration"]["status"] == "confirmed"
        assert body["entryDiscountPercent"] == 67

    def test_list_query_count(self, api_client, django_assert_max_num_queries):
        for _ in range(8):
            tournament = TournamentFactory()
            confirm_player(tournament, UserFactory())

        with django_assert_max_num_queries(5):
            api_client.get(reverse("tournament-list"))


class TestSoloRegistration:
    def test_free_registration_is_confirmed(self, auth_client, user):
        tournament = TournamentFactory()

        response = auth_client.post(register_url(tournament), {}, format="json")

        assert response.status_code == 201
        assert response.json()["registration"]["status"] == "confirmed"
        assert Notification.objects.filter(user=user, kind="tournament").exists()

    def test_duplicate_registration(self, auth_client):
        tournament = TournamentFactory()
        auth_client.post(register_url(tournament))

        response = auth_client.post(register_url(tournament))

        assert response.status_code == 409

    def test_registration_window(self, auth_client):
        tournament = TournamentFactory(upcoming=True)

        assert auth_client.post(register_url(tournament)).json()["code"] == "registration_closed"

    def test_capacity(self, auth_client):
        tournament = TournamentFactory(max_participants=2)
        confirm_player(tournament, UserFactory())
        confirm_player(tournament, UserFactory())

        assert auth_client.post(register_url(tournament)).json()["code"] == "tournament_full"

    def test_paid_requires_payment_method(self, auth_client):
        tournament = TournamentFactory(entry_fee=50_000)

        assert auth_client.post(register_url(tournament)).json()["code"] == "payment_method_required"

    def test_paid_with_wallet(self, auth_client, user):
        tournament = TournamentFactory(entry_fee=50_000)
        fund(user, 60_000)

        body = auth_client.post(register_url(tournament), {"paymentMethod": "wallet"}, format="json").json()

        assert body["registration"]["status"] == "confirmed"
        assert body["registration"]["entryFeePaid"] == 50_000
        assert get_wallet(user).balance == 10_000

    def test_wallet_insufficient_rolls_back(self, auth_client):
        tournament = TournamentFactory(entry_fee=50_000)

        response = auth_client.post(register_url(tournament), {"paymentMethod": "wallet"}, format="json")

        assert response.status_code == 402
        assert not Registration.objects.exists()

    @responses.activate
    def test_paid_with_gateway(self, auth_client, api_client, user):
        responses.post(f"{ZARINPAL}/request.json", json={"data": {"code": 100, "authority": "A9"}})
        responses.post(f"{ZARINPAL}/verify.json", json={"data": {"code": 100, "ref_id": 1}})
        tournament = TournamentFactory(entry_fee=50_000)

        body = auth_client.post(register_url(tournament), {"paymentMethod": "gateway"}, format="json").json()
        assert body["registration"]["status"] == "pending_payment"
        api_client.get(reverse("payment-callback"), {"Authority": "A9", "Status": "OK"})

        assert Registration.objects.get().status == Registration.Status.CONFIRMED

    @responses.activate
    def test_full_by_payment_time_refunds(self, auth_client, api_client, user):
        responses.post(f"{ZARINPAL}/request.json", json={"data": {"code": 100, "authority": "A9"}})
        responses.post(f"{ZARINPAL}/verify.json", json={"data": {"code": 100, "ref_id": 1}})
        tournament = TournamentFactory(entry_fee=50_000, max_participants=2)
        confirm_player(tournament, UserFactory())
        auth_client.post(register_url(tournament), {"paymentMethod": "gateway"}, format="json")
        confirm_player(tournament, UserFactory())  # someone else took the last slot meanwhile

        api_client.get(reverse("payment-callback"), {"Authority": "A9", "Status": "OK"})

        assert Registration.objects.get(registered_by=user).status == Registration.Status.CANCELLED
        assert get_wallet(user).balance == 50_000

    @responses.activate
    def test_cancelled_gateway_payment_frees_registration(self, auth_client, api_client):
        responses.post(f"{ZARINPAL}/request.json", json={"data": {"code": 100, "authority": "A9"}})
        tournament = TournamentFactory(entry_fee=50_000)
        auth_client.post(register_url(tournament), {"paymentMethod": "gateway"}, format="json")

        api_client.get(reverse("payment-callback"), {"Authority": "A9", "Status": "NOK"})

        assert Registration.objects.get().status == Registration.Status.CANCELLED

    def test_team_not_allowed_in_solo(self, auth_client, user):
        team = team_with_members(1)

        response = auth_client.post(register_url(TournamentFactory()), {"team": team.pk}, format="json")

        assert response.json()["code"] == "solo_tournament"


class TestTeamRegistration:
    @pytest.fixture
    def tournament(self):
        return TournamentFactory(team=True)  # team_size=2

    def test_captain_registers_team_with_roster(self, client_for, tournament):
        team = team_with_members(3, game=tournament.game)

        response = client_for(captain_of(team)).post(
            register_url(tournament), {"team": team.pk}, format="json"
        )

        assert response.status_code == 201
        assert Registration.objects.get().members.count() == 3

    def test_team_required(self, client_for, tournament):
        team = team_with_members(2, game=tournament.game)

        response = client_for(captain_of(team)).post(register_url(tournament))

        assert response.json()["code"] == "team_required"

    def test_only_captain(self, client_for, tournament):
        team = team_with_members(2, game=tournament.game)
        member = team.memberships.get(role="player").user

        response = client_for(member).post(register_url(tournament), {"team": team.pk}, format="json")

        assert response.json()["code"] == "not_captain"

    def test_team_too_small(self, client_for, tournament):
        team = team_with_members(1, game=tournament.game)

        response = client_for(captain_of(team)).post(
            register_url(tournament), {"team": team.pk}, format="json"
        )

        assert response.json()["code"] == "team_too_small"

    def test_game_mismatch(self, client_for, tournament):
        from apps.catalog.tests.factories import GameFactory

        team = team_with_members(2, game=GameFactory(slug="fortnite"))

        response = client_for(captain_of(team)).post(
            register_url(tournament), {"team": team.pk}, format="json"
        )

        assert response.json()["code"] == "team_game_mismatch"

    def test_roster_conflict_between_teams(self, client_for, tournament):
        first = team_with_members(2, game=tournament.game)
        confirm_team(tournament, first)
        second = team_with_members(2, game=tournament.game)
        shared_player = first.memberships.get(role="player").user
        second.memberships.create(user=shared_player)

        response = client_for(captain_of(second)).post(
            register_url(tournament), {"team": second.pk}, format="json"
        )

        assert response.json()["code"] == "roster_conflict"

    def test_eligible_teams(self, client_for, tournament):
        eligible = team_with_members(2, game=tournament.game)
        captain = captain_of(eligible)
        registered = team_with_members(2, game=tournament.game)
        registered.memberships.filter(role="captain").update(user=captain)
        confirm_team(tournament, registered)

        body = client_for(captain).get(reverse("tournament-eligible-teams", args=[tournament.slug])).json()

        assert [team["id"] for team in body] == [eligible.pk]


class TestWithdraw:
    def test_withdraw_refunds_paid_entry(self, auth_client, user):
        tournament = TournamentFactory(entry_fee=50_000)
        fund(user, 50_000)
        auth_client.post(register_url(tournament), {"paymentMethod": "wallet"}, format="json")

        response = auth_client.delete(register_url(tournament))

        assert response.status_code == 204
        assert Registration.objects.get().status == Registration.Status.WITHDRAWN
        assert get_wallet(user).balance == 50_000

    def test_can_register_again_after_withdrawing(self, auth_client):
        tournament = TournamentFactory()
        auth_client.post(register_url(tournament))
        auth_client.delete(register_url(tournament))

        assert auth_client.post(register_url(tournament)).status_code == 201

    def test_not_registered(self, auth_client):
        assert auth_client.delete(register_url(TournamentFactory())).json()["code"] == "not_registered"

    def test_cannot_withdraw_once_live(self, auth_client, user):
        tournament = TournamentFactory(state=Tournament.State.LIVE)
        confirm_player(tournament, user)

        assert auth_client.delete(register_url(tournament)).json()["code"] == "withdraw_closed"

    def test_team_member_cannot_withdraw_team(self, client_for):
        tournament = TournamentFactory(team=True)
        team = team_with_members(2, game=tournament.game)
        confirm_team(tournament, team)
        member = team.memberships.get(role="player").user

        assert client_for(member).delete(register_url(tournament)).json()["code"] == "not_registrant"
