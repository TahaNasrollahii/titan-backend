from django.urls import reverse

import pytest

from apps.accounts.tests.factories import BadgeFactory, UserFactory
from apps.core.exceptions import DomainError
from apps.payments.services import get_wallet
from apps.teams.tests.factories import team_with_members
from apps.tournaments.models import Match, PlayerStats, Registration, TeamStats, Tournament, TournamentPrize
from apps.tournaments.services import bracket, results
from apps.tournaments.services.bracket import seeding_order
from apps.tournaments.tests.factories import TournamentFactory, confirm_player, confirm_team

pytestmark = pytest.mark.django_db


def solo_tournament_with(entrants: int, **kwargs) -> Tournament:
    tournament = TournamentFactory(**kwargs)
    for points in range(entrants, 0, -1):  # first created = highest rated = seed 1
        confirm_player(tournament, UserFactory(points=points * 100))
    return tournament


def play_out(tournament: Tournament) -> None:
    """Report every playable match, the higher seed (participant A) always winning."""
    while tournament.matches.filter(status=Match.Status.SCHEDULED).exists():
        for match in tournament.matches.filter(status=Match.Status.SCHEDULED):
            results.report_result(match, score_a=13, score_b=5)
        tournament.refresh_from_db()


class TestSeeding:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [(2, [1, 2]), (4, [1, 4, 2, 3]), (8, [1, 8, 4, 5, 2, 7, 3, 6])],
    )
    def test_standard_order(self, size, expected):
        assert seeding_order(size) == expected


class TestGenerate:
    def test_power_of_two_bracket(self):
        tournament = solo_tournament_with(8)

        bracket.generate_bracket(tournament)

        tournament.refresh_from_db()
        assert tournament.state == Tournament.State.LIVE
        assert list(tournament.matches.values_list("round", flat=True)).count(1) == 4
        assert tournament.matches.count() == 7
        first = tournament.matches.get(round=1, position=0)
        assert (first.participant_a.seed, first.participant_b.seed) == (1, 8)
        final = tournament.matches.get(round=3)
        assert final.next_match is None

    def test_byes_auto_advance(self):
        tournament = solo_tournament_with(5)

        bracket.generate_bracket(tournament)

        byes = tournament.matches.filter(status=Match.Status.BYE)
        assert byes.count() == 3  # seeds 1, 2, 3 skip round one
        assert sorted(byes.values_list("winner__seed", flat=True)) == [1, 2, 3]
        semi = tournament.matches.get(round=2, position=1)  # winners of (2 v bye) and (3 v bye)
        assert semi.status == Match.Status.SCHEDULED

    def test_pending_payments_are_cancelled(self):
        tournament = solo_tournament_with(2)
        pending = UserFactory()
        Registration.objects.create(
            tournament=tournament, player=pending, registered_by=pending, status="pending_payment"
        )

        bracket.generate_bracket(tournament)

        assert Registration.objects.get(player=pending).status == Registration.Status.CANCELLED

    def test_needs_two_participants(self):
        tournament = solo_tournament_with(1)

        with pytest.raises(DomainError) as error:
            bracket.generate_bracket(tournament)
        assert error.value.code == "not_enough_participants"

    def test_cannot_generate_twice(self):
        tournament = solo_tournament_with(2)
        bracket.generate_bracket(tournament)

        with pytest.raises(DomainError) as error:
            bracket.generate_bracket(tournament)
        assert error.value.code == "invalid_state"

    def test_staff_only_endpoint(self, auth_client, client_for, staff_user):
        tournament = solo_tournament_with(4)
        url = reverse("tournament-generate-bracket", args=[tournament.slug])

        assert auth_client.post(url).status_code == 403
        response = client_for(staff_user).post(url)
        assert response.status_code == 201
        assert [r["name"] for r in response.json()] == ["نیمه‌نهایی", "فینال"]


class TestResults:
    def test_winner_advances_and_loser_placed(self):
        tournament = solo_tournament_with(4)
        bracket.generate_bracket(tournament)
        match = tournament.matches.get(round=1, position=0)

        results.report_result(match, score_a=2, score_b=13)

        match.refresh_from_db()
        assert match.winner == match.participant_b
        final = tournament.matches.get(round=2)
        assert final.participant_a == match.participant_b
        match.participant_a.refresh_from_db()
        assert match.participant_a.final_placement == 3

    def test_draws_rejected(self):
        tournament = solo_tournament_with(2)
        bracket.generate_bracket(tournament)

        with pytest.raises(DomainError) as error:
            results.report_result(tournament.matches.get(), score_a=1, score_b=1)
        assert error.value.code == "draw_not_allowed"

    def test_cannot_report_twice(self):
        tournament = solo_tournament_with(4)
        bracket.generate_bracket(tournament)
        match = tournament.matches.get(round=1, position=0)
        results.report_result(match, score_a=2, score_b=1)

        with pytest.raises(DomainError) as error:
            results.report_result(match, score_a=2, score_b=1)
        assert error.value.code == "match_finished"

    def test_full_tournament_completion(self):
        BadgeFactory(slug="champion")
        tournament = solo_tournament_with(4, prize_pool=3000, prize_currency="IRT")
        TournamentPrize.objects.create(tournament=tournament, place=1, amount=2000, points=500)
        TournamentPrize.objects.create(tournament=tournament, place=3, amount=1000, points=100)
        bracket.generate_bracket(tournament)

        play_out(tournament)

        tournament.refresh_from_db()
        assert tournament.state == Tournament.State.COMPLETED
        placements = dict(tournament.registrations.values_list("seed", "final_placement"))
        assert placements == {1: 1, 2: 2, 3: 3, 4: 3}
        champion = tournament.registrations.get(final_placement=1).player
        assert get_wallet(champion).balance == 2000
        semi_loser = tournament.registrations.filter(final_placement=3).first().player
        assert get_wallet(semi_loser).balance == 500  # third place prize split two ways
        stats = PlayerStats.objects.get(user=champion)
        assert (stats.wins, stats.matches, stats.tournaments_won, stats.earnings_irt) == (2, 2, 1, 2000)
        assert champion.user_badges.filter(badge__slug="champion").exists()
        champion.refresh_from_db()
        assert champion.points == 400 + 2 * results.MATCH_WIN_POINTS + 500

    def test_usd_prizes_are_recorded_but_not_paid_out(self):
        tournament = solo_tournament_with(2, prize_currency="USD")
        TournamentPrize.objects.create(tournament=tournament, place=1, amount=5000)
        bracket.generate_bracket(tournament)

        play_out(tournament)

        champion = tournament.registrations.get(final_placement=1).player
        assert get_wallet(champion).balance == 0
        assert PlayerStats.objects.get(user=champion).earnings_usd == 5000

    def test_team_stats_and_counters(self):
        tournament = TournamentFactory(team=True)
        winners = team_with_members(2, points=900)
        losers = team_with_members(2, points=100)
        confirm_team(tournament, winners)
        confirm_team(tournament, losers)
        bracket.generate_bracket(tournament)

        play_out(tournament)

        winners.refresh_from_db()
        assert (winners.wins, winners.matches_played) == (1, 1)
        assert TeamStats.objects.get(team=winners).tournaments_won == 1
        for membership in winners.memberships.all():
            assert PlayerStats.objects.get(user=membership.user).wins == 1


class TestMatchEndpoint:
    def test_staff_reports_score(self, client_for, staff_user):
        tournament = solo_tournament_with(2)
        bracket.generate_bracket(tournament)
        match = tournament.matches.get()

        response = client_for(staff_user).patch(
            reverse("match-detail", args=[match.pk]), {"scoreA": 13, "scoreB": 7}, format="json"
        )

        assert response.status_code == 200
        assert response.json()["status"] == "completed"

    def test_set_live_and_lobby_code(self, client_for, staff_user):
        tournament = solo_tournament_with(2)
        bracket.generate_bracket(tournament)
        match = tournament.matches.get()

        body = (
            client_for(staff_user)
            .patch(
                reverse("match-detail", args=[match.pk]),
                {"status": "live", "lobbyCode": "LOBBY-1"},
                format="json",
            )
            .json()
        )

        assert body["status"] == "live"

    def test_needs_both_scores(self, client_for, staff_user):
        tournament = solo_tournament_with(2)
        bracket.generate_bracket(tournament)

        response = client_for(staff_user).patch(
            reverse("match-detail", args=[tournament.matches.get().pk]), {"scoreA": 13}, format="json"
        )

        assert response.status_code == 400

    def test_players_cannot_report(self, auth_client):
        tournament = solo_tournament_with(2)
        bracket.generate_bracket(tournament)

        response = auth_client.patch(
            reverse("match-detail", args=[tournament.matches.get().pk]),
            {"scoreA": 1, "scoreB": 0},
            format="json",
        )

        assert response.status_code == 403


class TestBracketView:
    def test_lobby_code_only_visible_to_participants(self, client_for, api_client):
        tournament = solo_tournament_with(2)
        bracket.generate_bracket(tournament)
        match = tournament.matches.get()
        results.update_match(match, lobby_code="SECRET")
        player = match.participant_a.player
        url = reverse("tournament-bracket", args=[tournament.slug])

        mine = client_for(player).get(url).json()[0]["matches"][0]
        public = api_client.get(url).json()[0]["matches"][0]

        assert (mine["isMine"], mine["lobbyCode"]) == (True, "SECRET")
        assert (public["isMine"], public["lobbyCode"]) == (False, None)

    def test_round_names(self, api_client):
        tournament = solo_tournament_with(16)
        bracket.generate_bracket(tournament)

        names = [
            r["name"] for r in api_client.get(reverse("tournament-bracket", args=[tournament.slug])).json()
        ]

        assert names == ["مرحله 16 تیمی", "یک‌چهارم نهایی", "نیمه‌نهایی", "فینال"]

    def test_participants_endpoint(self, api_client):
        tournament = solo_tournament_with(3)

        body = api_client.get(reverse("tournament-participants", args=[tournament.slug])).json()

        assert len(body) == 3
        assert body[0]["kind"] == "player"
