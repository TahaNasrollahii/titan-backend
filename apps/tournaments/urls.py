from django.urls import path

from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("tournaments", views.TournamentViewSet, basename="tournament")
router.register("matches", views.MatchViewSet, basename="match")

urlpatterns = [
    path("matches/upcoming/", views.UpcomingMatchListView.as_view(), name="upcoming-matches"),
    path("seasons/", views.SeasonListView.as_view(), name="season-list"),
    path("leaderboards/players/", views.PlayerLeaderboardView.as_view(), name="leaderboard-players"),
    path("leaderboards/teams/", views.TeamLeaderboardView.as_view(), name="leaderboard-teams"),
    path("me/tournaments/", views.MyTournamentListView.as_view(), name="my-tournaments"),
    path("me/stats/", views.MyStatsView.as_view(), name="my-stats"),
    path("players/<str:username>/", views.PlayerProfileView.as_view(), name="player-profile"),
    path("teams/<int:team_id>/tournaments/", views.TeamTournamentListView.as_view(), name="team-tournaments"),
    *router.urls,
]
