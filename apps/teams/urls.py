from django.urls import path

from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("teams", views.TeamViewSet, basename="team")
router.register("me/team-invitations", views.MyTeamInvitationViewSet, basename="my-team-invitation")

urlpatterns = [
    path("me/teams/", views.MyTeamListView.as_view(), name="my-teams"),
    *router.urls,
]
