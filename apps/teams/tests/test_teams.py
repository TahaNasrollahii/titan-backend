from django.urls import reverse
from django.utils import timezone

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.notifications.models import Notification
from apps.teams.models import MAX_TEAM_MEMBERS, Team, TeamInvitation, TeamMembership
from apps.teams.tests.factories import MembershipFactory, TeamFactory, captain_of, team_with_members
from apps.tournaments.tests.factories import TournamentFactory, confirm_team

pytestmark = pytest.mark.django_db

TEAMS_URL = reverse("team-list")


def team_url(team, suffix=""):
    return f"{TEAMS_URL}{team.pk}/{suffix}"


class TestCreateAndUpdate:
    def test_creator_becomes_captain(self, auth_client, user):
        response = auth_client.post(TEAMS_URL, {"name": "  Iran Titans "}, format="json")

        body = response.json()
        assert response.status_code == 201
        assert body["name"] == "Iran Titans"
        assert body["maxMembers"] == 10
        assert body["myRole"] == "captain"
        assert body["inviteCode"] and body["inviteUrl"].endswith(body["inviteCode"])
        assert [m["user"]["id"] for m in body["members"]] == [user.pk]

    def test_name_is_unique_case_insensitive(self, auth_client):
        TeamFactory(name="Iran Titans")

        response = auth_client.post(TEAMS_URL, {"name": "iran titans"})

        assert response.status_code == 409
        assert response.json()["code"] == "team_name_taken"

    def test_dissolved_team_name_can_be_reused(self, auth_client):
        TeamFactory(name="Phoenix", dissolved_at=timezone.now())

        assert auth_client.post(TEAMS_URL, {"name": "Phoenix"}).status_code == 201

    def test_name_required(self, auth_client):
        assert auth_client.post(TEAMS_URL, {"name": ""}).status_code == 400

    def test_only_captain_can_update(self, client_for):
        team = team_with_members(2)
        member = team.memberships.get(role="player").user

        assert client_for(member).patch(team_url(team), {"name": "Hacked"}).status_code == 403
        response = client_for(captain_of(team)).patch(team_url(team), {"name": "Renamed"})
        assert response.json()["name"] == "Renamed"

    def test_invite_code_hidden_from_non_captains(self, api_client, client_for):
        team = team_with_members(2)
        member = team.memberships.get(role="player").user

        assert api_client.get(team_url(team)).json()["inviteCode"] is None
        assert client_for(member).get(team_url(team)).json()["inviteCode"] is None

    def test_list_search_by_name(self, api_client):
        TeamFactory(name="Valkyries")
        TeamFactory(name="Phoenix")

        names = [team["name"] for team in api_client.get(TEAMS_URL, {"search": "valk"}).json()["results"]]

        assert names == ["Valkyries"]


class TestJoining:
    def test_join_with_code(self, auth_client, user):
        team = team_with_members(1)

        response = auth_client.post(reverse("team-join"), {"code": team.invite_code.lower()}, format="json")

        assert response.status_code == 201
        assert team.memberships.filter(user=user, role="player").exists()

    def test_invalid_code(self, auth_client):
        assert (
            auth_client.post(reverse("team-join"), {"code": "NOPE"}).json()["code"] == "invalid_invite_code"
        )

    def test_cannot_join_twice(self, auth_client, user):
        team = team_with_members(1)
        MembershipFactory(team=team, user=user)

        assert auth_client.post(reverse("team-join"), {"code": team.invite_code}).status_code == 409

    def test_full_team(self, auth_client):
        team = team_with_members(MAX_TEAM_MEMBERS)

        assert (
            auth_client.post(reverse("team-join"), {"code": team.invite_code}).json()["code"] == "team_full"
        )

    def test_regenerate_invite_code(self, client_for):
        team = team_with_members(1)
        old = team.invite_code

        body = client_for(captain_of(team)).post(team_url(team, "invite-code/regenerate/")).json()

        assert body["inviteCode"] != old


class TestInvitations:
    def test_invite_accept_flow(self, client_for, user):
        team = team_with_members(1)
        captain = client_for(captain_of(team))

        assert captain.post(team_url(team, "invitations/"), {"username": user.username}).status_code == 201
        notification = Notification.objects.get(user=user, kind="team_invite")
        invitation_id = notification.data["invitation_id"]

        me = client_for(user)
        assert len(me.get(reverse("my-team-invitation-list")).json()) == 1
        response = me.post(reverse("my-team-invitation-accept", args=[invitation_id]))

        assert response.json()["myRole"] == "player"
        notification.refresh_from_db()
        assert notification.is_read
        assert Notification.objects.filter(user=captain_of(team), kind="team").exists()

    def test_decline(self, client_for, user):
        team = team_with_members(1)
        client_for(captain_of(team)).post(team_url(team, "invitations/"), {"username": user.username})
        invitation = TeamInvitation.objects.get()

        response = client_for(user).post(reverse("my-team-invitation-decline", args=[invitation.pk]))

        assert response.json()["status"] == "declined"
        assert not team.memberships.filter(user=user).exists()

    def test_duplicate_pending_invitation(self, client_for, user):
        team = team_with_members(1)
        captain = client_for(captain_of(team))
        captain.post(team_url(team, "invitations/"), {"username": user.username})

        response = captain.post(team_url(team, "invitations/"), {"username": user.username})

        assert response.json()["code"] == "invitation_pending"

    def test_only_captain_invites_and_sees_invitations(self, client_for, user):
        team = team_with_members(2)
        member = client_for(team.memberships.get(role="player").user)

        assert member.post(team_url(team, "invitations/"), {"username": user.username}).status_code == 403
        assert member.get(team_url(team, "invitations/")).json() == []

    def test_cancel_invitation(self, client_for, user):
        team = team_with_members(1)
        captain = client_for(captain_of(team))
        invitation_id = captain.post(team_url(team, "invitations/"), {"username": user.username}).json()["id"]

        assert captain.delete(team_url(team, f"invitations/{invitation_id}/")).status_code == 204
        assert TeamInvitation.objects.get().status == "cancelled"


class TestRoster:
    def test_kick_member(self, client_for):
        team = team_with_members(2)
        member = team.memberships.get(role="player").user

        response = client_for(captain_of(team)).delete(team_url(team, f"members/{member.pk}/"))

        assert response.status_code == 204
        assert not team.memberships.filter(user=member).exists()
        assert Notification.objects.filter(user=member, kind="team").exists()

    def test_member_cannot_kick(self, client_for):
        team = team_with_members(3)
        first, second = [m.user for m in team.memberships.filter(role="player")]

        assert client_for(first).delete(team_url(team, f"members/{second.pk}/")).status_code == 403

    def test_member_can_leave(self, client_for):
        team = team_with_members(2)
        member = team.memberships.get(role="player").user

        assert client_for(member).delete(team_url(team, f"members/{member.pk}/")).status_code == 204

    def test_captain_must_transfer_before_leaving(self, client_for):
        team = team_with_members(2)
        captain = captain_of(team)

        response = client_for(captain).delete(team_url(team, f"members/{captain.pk}/"))

        assert response.json()["code"] == "captain_must_transfer"

    def test_last_member_leaving_dissolves_team(self, client_for):
        team = team_with_members(1)
        captain = captain_of(team)

        client_for(captain).delete(team_url(team, f"members/{captain.pk}/"))

        team.refresh_from_db()
        assert team.dissolved_at is not None

    def test_transfer_captaincy(self, client_for):
        team = team_with_members(2)
        old_captain = captain_of(team)
        member = team.memberships.get(role="player").user

        body = client_for(old_captain).post(team_url(team, f"members/{member.pk}/promote/")).json()

        assert body["myRole"] == "player"
        assert captain_of(team) == member
        assert TeamMembership.objects.filter(team=team, role="captain").count() == 1


class TestDissolve:
    def test_captain_dissolves(self, client_for):
        team = team_with_members(2)
        member = team.memberships.get(role="player").user

        assert client_for(captain_of(team)).delete(team_url(team)).status_code == 204
        team.refresh_from_db()
        assert team.dissolved_at is not None
        assert not team.memberships.exists()
        assert Notification.objects.filter(user=member, kind="team").exists()
        assert client_for(member).get(team_url(team)).status_code == 404

    def test_cannot_dissolve_while_in_active_tournament(self, client_for):
        team = team_with_members(2)
        confirm_team(TournamentFactory(team=True), team)

        response = client_for(captain_of(team)).delete(team_url(team))

        assert response.json()["code"] == "team_in_tournament"


class TestLineupLock:
    def lineup_member(self, team):
        return team.memberships.get(role="player").user

    def test_lineup_member_cannot_be_kicked_or_leave(self, client_for):
        team = team_with_members(2)
        confirm_team(TournamentFactory(team=True), team)
        member = self.lineup_member(team)

        kick = client_for(captain_of(team)).delete(team_url(team, f"members/{member.pk}/"))
        leave = client_for(member).delete(team_url(team, f"members/{member.pk}/"))

        assert kick.json()["code"] == leave.json()["code"] == "member_in_lineup"

    def test_bench_player_can_leave(self, client_for):
        team = team_with_members(3)
        confirm_team(TournamentFactory(team=True), team)  # the first two members play
        bench = team.memberships.order_by("joined_at").last().user

        assert client_for(bench).delete(team_url(team, f"members/{bench.pk}/")).status_code == 204

    def test_lock_lifts_once_the_tournament_is_over(self, client_for):
        from apps.tournaments.models import Tournament

        team = team_with_members(2)
        confirm_team(TournamentFactory(team=True, state=Tournament.State.COMPLETED), team)
        member = self.lineup_member(team)

        assert client_for(member).delete(team_url(team, f"members/{member.pk}/")).status_code == 204


class TestMyTeams:
    def test_rail_activity(self, client_for):
        team = team_with_members(2)
        captain = captain_of(team)
        member = team.memberships.get(role="player").user
        member.presence_status = "in_game"
        member.last_seen = timezone.now()
        member.save()

        body = client_for(captain).get(reverse("my-teams")).json()

        assert len(body) == 1
        assert body[0]["activity"] == "in_game"
        assert body[0]["myRole"] == "captain"

    def test_offline_team(self, client_for):
        team = team_with_members(1)

        assert client_for(captain_of(team)).get(reverse("my-teams")).json()[0]["activity"] == "offline"

    def test_excludes_dissolved_teams(self, client_for, user):
        MembershipFactory(user=user, team=TeamFactory(dissolved_at=timezone.now()))

        assert client_for(user).get(reverse("my-teams")).json() == []


def test_team_detail_query_count(api_client, django_assert_max_num_queries):
    team = team_with_members(5)
    for membership in team.memberships.all():
        membership.user.points = 100
        membership.user.save()
    UserFactory.create_batch(3)

    with django_assert_max_num_queries(6):
        api_client.get(team_url(team))


def test_team_model_win_rate():
    assert Team(matches_played=4, wins=3).win_rate == 75.0
    assert Team().win_rate == 0.0
