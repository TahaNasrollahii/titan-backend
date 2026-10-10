from django.shortcuts import get_object_or_404

from drf_spectacular.utils import extend_schema
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from apps.accounts.models import User

from . import services
from .models import Team, TeamInvitation, TeamMembership
from .serializers import (
    InvitePlayerSerializer,
    JoinTeamSerializer,
    MyTeamSerializer,
    TeamDetailSerializer,
    TeamInvitationSerializer,
    TeamListSerializer,
    TeamMemberSerializer,
    TeamWriteSerializer,
)


@extend_schema(tags=["teams"])
class TeamViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Teams. Updating, inviting and managing members is restricted to the captain."""

    permission_classes = [IsAuthenticatedOrReadOnly]
    http_method_names = ["get", "post", "patch", "delete"]
    search_fields = ["name"]
    ordering_fields = ["points", "created_at", "name"]
    ordering = ["-points", "name"]

    def get_queryset(self):
        return services.teams_with_members()

    def get_serializer_class(self):
        if self.action in {"create", "partial_update"}:
            return TeamWriteSerializer
        if self.action == "list":
            return TeamListSerializer
        return TeamDetailSerializer

    def _detail(self, team: Team, status_code=status.HTTP_200_OK) -> Response:
        team = self.get_queryset().get(pk=team.pk)
        data = TeamDetailSerializer(team, context=self.get_serializer_context()).data
        return Response(data, status=status_code)

    @extend_schema(request=TeamWriteSerializer, responses={201: TeamDetailSerializer})
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team = services.create_team(request.user, **serializer.validated_data)
        return self._detail(team, status.HTTP_201_CREATED)

    @extend_schema(request=TeamWriteSerializer, responses=TeamDetailSerializer)
    def partial_update(self, request, *args, **kwargs):
        team = self.get_object()
        serializer = self.get_serializer(team, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.update_team(team, request.user, **serializer.validated_data)
        return self._detail(team)

    @extend_schema(responses={204: None})
    def destroy(self, request, *args, **kwargs):
        services.dissolve_team(self.get_object(), request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses=TeamMemberSerializer(many=True))
    @action(detail=True, methods=["get"], pagination_class=None, filter_backends=[])
    def members(self, request, pk=None):
        team = self.get_object()
        return Response(
            TeamMemberSerializer(team.memberships.all(), many=True, context={"request": request}).data
        )

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=["delete"], url_path=r"members/(?P<user_id>\d+)")
    def remove_member(self, request, pk=None, user_id=None):
        member = get_object_or_404(User, pk=user_id)
        services.remove_member(self.get_object(), request.user, member)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses=TeamDetailSerializer)
    @action(detail=True, methods=["post"], url_path=r"members/(?P<user_id>\d+)/promote")
    def promote(self, request, pk=None, user_id=None):
        team = self.get_object()
        services.transfer_captaincy(team, request.user, get_object_or_404(User, pk=user_id))
        return self._detail(team)

    @extend_schema(request=None, responses=TeamDetailSerializer)
    @action(detail=True, methods=["post"], url_path="invite-code/regenerate")
    def regenerate_invite_code(self, request, pk=None):
        team = self.get_object()
        services.regenerate_invite_code(team, request.user)
        return self._detail(team)

    @extend_schema(methods=["get"], responses=TeamInvitationSerializer(many=True))
    @extend_schema(
        methods=["post"], request=InvitePlayerSerializer, responses={201: TeamInvitationSerializer}
    )
    @action(detail=True, methods=["get", "post"], pagination_class=None, filter_backends=[])
    def invitations(self, request, pk=None):
        team = self.get_object()
        if request.method == "GET":
            pending = team.invitations.filter(status=TeamInvitation.Status.PENDING).select_related(
                "team", "invited_user", "invited_by"
            )
            if not team.memberships.filter(user=request.user, role=TeamMembership.Role.CAPTAIN).exists():
                pending = pending.none()
            return Response(TeamInvitationSerializer(pending, many=True, context={"request": request}).data)

        serializer = InvitePlayerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = services.invite(team, request.user, serializer.validated_data["username"])
        data = TeamInvitationSerializer(invitation, context={"request": request}).data
        return Response(data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=["delete"], url_path=r"invitations/(?P<invitation_id>\d+)")
    def cancel_invitation(self, request, pk=None, invitation_id=None):
        services.cancel_invitation(self.get_object(), request.user, int(invitation_id))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=JoinTeamSerializer, responses={201: TeamDetailSerializer})
    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def join(self, request):
        serializer = JoinTeamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.join_with_code(request.user, serializer.validated_data["code"])
        return self._detail(membership.team, status.HTTP_201_CREATED)


@extend_schema(tags=["teams"])
class MyTeamListView(generics.ListAPIView):
    """Teams the current user belongs to, with aggregated member presence (``activity``) for the rail."""

    serializer_class = MyTeamSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    filter_backends: list = []

    def get_queryset(self):
        return services.teams_with_members().filter(memberships__user=self.request.user).order_by("name")


@extend_schema(tags=["teams"])
class MyTeamInvitationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = TeamInvitation.objects.none()  # model hint for the schema; real queryset is per-user
    serializer_class = TeamInvitationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    filter_backends: list = []

    def get_queryset(self):
        return TeamInvitation.objects.filter(
            invited_user=self.request.user,
            status=TeamInvitation.Status.PENDING,
            team__dissolved_at__isnull=True,
        ).select_related("team", "invited_user", "invited_by")

    @extend_schema(request=None, responses=TeamDetailSerializer)
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        membership = services.accept_invitation(request.user, int(pk))
        team = services.teams_with_members().get(pk=membership.team_id)
        return Response(TeamDetailSerializer(team, context={"request": request}).data)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        invitation = services.decline_invitation(request.user, int(pk))
        return Response(self.get_serializer(invitation).data)
