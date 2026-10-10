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


def members_of(team, count=None):
    ids = list(team.memberships.values_list("user_id", flat=True))
    return ids if count is None else ids[:count]


class TestTeamRegistration:
    @pytest.fixture
    def tournament(self):
        return TournamentFactory(team=True)  # team_size=2

    def register_team(self, client_for, tournament, team, members):
        return client_for(captain_of(team)).post(
            register_url(tournament), {"team": team.pk, "members": members}, format="json"
        )

    def test_captain_registers_a_lineup_from_a_bigger_team(self, client_for, tournament):
        team = team_with_members(10)
        lineup = members_of(team)[3:5]

        response = self.register_team(client_for, tournament, team, lineup)

        assert response.status_code == 201
        registration = Registration.objects.get()
        assert sorted(registration.members.values_list("user_id", flat=True)) == sorted(lineup)
        body = response.json()["registration"]
        assert sorted(member["id"] for member in body["members"]) == sorted(lineup)
        assert body["canManage"] is True

    def test_captain_does_not_have_to_play(self, client_for, tournament):
        team = team_with_members(3)
        captain = captain_of(team)
        lineup = [pk for pk in members_of(team) if pk != captain.pk]

        response = self.register_team(client_for, tournament, team, lineup)

        assert response.status_code == 201
        detail = client_for(captain).get(reverse("tournament-detail", args=[tournament.slug])).json()
        assert detail["isRegistered"] is True
        assert detail["myRegistration"]["canManage"] is True

    @pytest.mark.parametrize("count", [1, 3])
    def test_lineup_must_match_team_size(self, client_for, tournament, count):
        team = team_with_members(5)

        response = self.register_team(client_for, tournament, team, members_of(team, count))

        assert response.json()["code"] == "lineup_size"

    def test_duplicate_players_rejected(self, client_for, tournament):
        team = team_with_members(3)
        player = members_of(team)[1]

        response = self.register_team(client_for, tournament, team, [player, player])

        assert response.json()["code"] == "lineup_size"

    def test_lineup_players_must_be_members(self, client_for, tournament):
        team = team_with_members(2)
        outsider = UserFactory()

        response = self.register_team(client_for, tournament, team, [members_of(team)[0], outsider.pk])

        assert response.json()["code"] == "not_member"

    def test_team_required(self, client_for, tournament):
        team = team_with_members(2)

        response = client_for(captain_of(team)).post(register_url(tournament))

        assert response.json()["code"] == "team_required"

    def test_only_captain(self, client_for, tournament):
        team = team_with_members(2)
        member = team.memberships.get(role="player").user

        response = client_for(member).post(
            register_url(tournament), {"team": team.pk, "members": members_of(team)}, format="json"
        )

        assert response.json()["code"] == "not_captain"

    def test_team_registers_once(self, client_for, tournament):
        team = team_with_members(4)
        confirm_team(tournament, team)

        response = self.register_team(client_for, tournament, team, members_of(team)[2:4])

        assert response.json()["code"] == "already_registered"

    def test_player_in_two_teams_plays_for_one(self, client_for, tournament):
        first = team_with_members(2)
        confirm_team(tournament, first)
        second = team_with_members(2)
        shared_player = first.memberships.get(role="player").user
        second.memberships.create(user=shared_player)

        response = self.register_team(
            client_for, tournament, second, [captain_of(second).pk, shared_player.pk]
        )

        assert response.json()["code"] == "roster_conflict"

    def test_member_left_out_of_a_lineup_can_play_for_another_team(self, client_for, tournament):
        first = team_with_members(3)
        bench = first.memberships.order_by("joined_at").last().user
        confirm_team(tournament, first, lineup=[m.user for m in first.memberships.exclude(user=bench)])
        second = team_with_members(2)
        second.memberships.create(user=bench)

        response = self.register_team(client_for, tournament, second, [captain_of(second).pk, bench.pk])

        assert response.status_code == 201

    def test_eligible_teams_list_members_and_who_is_taken(self, client_for, tournament):
        eligible = team_with_members(3)
        captain = captain_of(eligible)
        registered = team_with_members(2)
        registered.memberships.filter(role="captain").update(user=captain)
        other = team_with_members(2, name="Rivals")
        taken = other.memberships.get(role="player").user
        eligible.memberships.create(user=taken)
        confirm_team(tournament, registered)
        confirm_team(tournament, other)

        body = client_for(captain).get(reverse("tournament-eligible-teams", args=[tournament.slug])).json()

        assert [team["id"] for team in body] == [eligible.pk]
        registered_with = {member["user"]["id"]: member["registeredWith"] for member in body[0]["members"]}
        assert registered_with[taken.pk] == "Rivals"
        assert registered_with[captain.pk] == registered.name


class TestLineup:
    @pytest.fixture
    def tournament(self):
        return TournamentFactory(team=True)

    def lineup_url(self, tournament):
        return reverse("tournament-lineup", args=[tournament.slug])

    def test_captain_swaps_a_player(self, client_for, tournament):
        team = team_with_members(3)
        first, _, third = members_of(team)
        registration = confirm_team(tournament, team)  # the first two members

        response = client_for(captain_of(team)).put(
            self.lineup_url(tournament), {"members": [first, third]}, format="json"
        )

        assert response.status_code == 200
        assert sorted(registration.members.values_list("user_id", flat=True)) == sorted([first, third])
        assert Notification.objects.filter(user_id=third, kind="tournament").exists()
        assert not Notification.objects.filter(user_id=first).exists()

    def test_player_cannot_edit_lineup(self, client_for, tournament):
        team = team_with_members(3)
        confirm_team(tournament, team)
        member = team.memberships.get(user_id=members_of(team)[1]).user

        response = client_for(member).put(
            self.lineup_url(tournament), {"members": members_of(team, 2)}, format="json"
        )

        assert response.json()["code"] == "not_captain"

    def test_locked_after_registration_closes(self, client_for, tournament):
        team = team_with_members(3)
        confirm_team(tournament, team)
        Tournament.objects.filter(pk=tournament.pk).update(registration_closes_at=timezone.now())

        response = client_for(captain_of(team)).put(
            self.lineup_url(tournament), {"members": members_of(team)[1:3]}, format="json"
        )

        assert response.json()["code"] == "registration_closed"

    def test_cannot_pick_a_player_from_another_entry(self, client_for, tournament):
        team = team_with_members(3)
        confirm_team(tournament, team)
        other = team_with_members(2)
        taken = other.memberships.get(role="player").user
        team.memberships.create(user=taken)
        confirm_team(tournament, other)

        response = client_for(captain_of(team)).put(
            self.lineup_url(tournament), {"members": [captain_of(team).pk, taken.pk]}, format="json"
        )

        assert response.json()["code"] == "roster_conflict"


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
        team = team_with_members(2)
        confirm_team(tournament, team)
        member = team.memberships.get(role="player").user

        assert client_for(member).delete(register_url(tournament)).json()["code"] == "not_captain"

    def test_new_captain_withdraws_and_refund_goes_to_the_payer(self, client_for):
        tournament = TournamentFactory(team=True, entry_fee=40_000)
        team = team_with_members(2)
        old_captain = captain_of(team)
        fund(old_captain, 40_000)
        client_for(old_captain).post(
            register_url(tournament),
            {"team": team.pk, "members": members_of(team), "paymentMethod": "wallet"},
            format="json",
        )
        new_captain = team.memberships.get(role="player").user
        client_for(old_captain).post(reverse("team-promote", args=[team.pk, new_captain.pk]))

        response = client_for(new_captain).delete(register_url(tournament))

        assert response.status_code == 204
        assert get_wallet(old_captain).balance == 40_000
