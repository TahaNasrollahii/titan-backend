from django.urls import reverse

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.catalog.tests.factories import GameFactory
from apps.teams.tests.factories import team_with_members
from apps.tournaments.models import PlayerStats, TeamStats
from apps.tournaments.services import bracket, results
from apps.tournaments.tests.factories import SeasonFactory, TournamentFactory, confirm_player

pytestmark = pytest.mark.django_db


@pytest.fixture
def season():
    return SeasonFactory()


class TestLeaderboards:
    def test_players_ranked_by_points_with_ties(self, api_client, season):
        valorant = GameFactory(slug="valorant")
        for name, points in [("a", 500), ("b", 900), ("c", 500)]:
            PlayerStats.objects.create(
                user=UserFactory(username=name), game=valorant, season=season, points=points
            )

        rows = api_client.get(reverse("leaderboard-players"), {"game": "valorant"}).json()["results"]

        assert [(row["rank"], row["player"]["username"]) for row in rows] == [(1, "b"), (2, "a"), (2, "c")]

    def test_filters_by_game_and_season(self, api_client, season):
        old = SeasonFactory(number=2, is_current=False)
        valorant, fortnite = GameFactory(slug="valorant"), GameFactory(slug="fortnite")
        user = UserFactory()
        PlayerStats.objects.create(user=user, game=valorant, season=season, points=1)
        PlayerStats.objects.create(user=user, game=fortnite, season=season, points=1)
        PlayerStats.objects.create(user=user, game=valorant, season=old, points=1)

        current = api_client.get(reverse("leaderboard-players"), {"game": "valorant"}).json()
        previous = api_client.get(reverse("leaderboard-players"), {"game": "valorant", "season": 2}).json()

        assert current["count"] == 1
        assert previous["count"] == 1

    def test_unknown_season_404(self, api_client, season):
        assert api_client.get(reverse("leaderboard-players"), {"season": 99}).status_code == 404

    def test_team_leaderboard(self, api_client, season):
        team = team_with_members(1)
        game = GameFactory(slug="valorant")
        TeamStats.objects.create(team=team, game=game, season=season, points=10, wins=3, matches=4)

        row = api_client.get(reverse("leaderboard-teams")).json()["results"][0]

        assert (row["team"]["name"], row["winRate"]) == (team.name, 75.0)


class TestPlayerStats:
    def test_my_stats_totals(self, auth_client, user, season, rank_tiers):
        user.points = 1600
        user.save()
        for slug, wins, losses in [("valorant", 3, 1), ("fortnite", 1, 3)]:
            PlayerStats.objects.create(
                user=user, game=GameFactory(slug=slug), season=season, wins=wins, losses=losses, matches=4
            )

        body = auth_client.get(reverse("my-stats")).json()

        assert (body["matches"], body["wins"], body["losses"], body["winRate"]) == (8, 4, 4, 50.0)
        assert body["rank"]["slug"] == "silver"

    def test_public_player_profile(self, api_client, season):
        player = UserFactory(username="ShadowStrike")
        PlayerStats.objects.create(user=player, game=GameFactory(), season=season, wins=1, matches=1)
        team = team_with_members(1)
        team.memberships.create(user=player)

        body = api_client.get(reverse("player-profile", args=["shadowstrike"])).json()

        assert body["player"]["username"] == "ShadowStrike"
        assert "phone" not in body["player"]
        assert body["totals"]["wins"] == 1
        assert [t["name"] for t in body["teams"]] == [team.name]

    def test_unknown_player(self, api_client):
        assert api_client.get(reverse("player-profile", args=["nobody"])).status_code == 404


class TestMyTournaments:
    def test_current_stage(self, auth_client, user):
        tournament = TournamentFactory()
        confirm_player(tournament, user)
        for _ in range(3):
            confirm_player(tournament, UserFactory())
        bracket.generate_bracket(tournament)

        entry = auth_client.get(reverse("my-tournaments")).json()["results"][0]

        assert entry["tournament"]["slug"] == tournament.slug
        assert entry["currentStage"] == "نیمه‌نهایی"

    def test_upcoming_matches(self, api_client):
        tournament = TournamentFactory()
        for _ in range(2):
            confirm_player(tournament, UserFactory())
        bracket.generate_bracket(tournament)

        rows = api_client.get(reverse("upcoming-matches")).json()["results"]

        assert len(rows) == 1
        assert rows[0]["tournament"]["slug"] == tournament.slug

    def test_team_tournament_history(self, api_client):
        from apps.tournaments.tests.factories import confirm_team

        tournament = TournamentFactory(team=True)
        team = team_with_members(2)
        confirm_team(tournament, team)

        rows = api_client.get(reverse("team-tournaments", args=[team.pk])).json()["results"]

        assert rows[0]["tournament"]["slug"] == tournament.slug


def test_placement_formula():
    assert [results.placement_for_loser(r, 3) for r in (3, 2, 1)] == [2, 3, 5]
