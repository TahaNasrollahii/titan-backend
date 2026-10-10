from django.conf import settings
from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.serializers import PresenceUserSerializer, UserMiniSerializer
from apps.core.serializers import ImageUrlField

from . import services
from .models import MAX_TEAM_MEMBERS, Team, TeamInvitation, TeamMembership


class TeamMemberSerializer(serializers.ModelSerializer):
    user = PresenceUserSerializer(read_only=True)

    class Meta:
        model = TeamMembership
        fields = ["user", "role", "joined_at"]


class TeamMiniSerializer(serializers.ModelSerializer):
    logo = ImageUrlField()

    class Meta:
        model = Team
        fields = ["id", "name", "logo"]


class TeamListSerializer(serializers.ModelSerializer):
    logo = ImageUrlField()
    member_count = serializers.IntegerField(read_only=True)
    max_members = serializers.SerializerMethodField()
    win_rate = serializers.FloatField(read_only=True)

    class Meta:
        model = Team
        fields = [
            "id",
            "name",
            "logo",
            "member_count",
            "max_members",
            "matches_played",
            "wins",
            "losses",
            "win_rate",
            "points",
            "created_at",
        ]

    def get_max_members(self, team: Team) -> int:
        return MAX_TEAM_MEMBERS


class TeamDetailSerializer(TeamListSerializer):
    members = TeamMemberSerializer(source="memberships", many=True, read_only=True)
    my_role = serializers.SerializerMethodField()
    invite_code = serializers.SerializerMethodField()
    invite_url = serializers.SerializerMethodField()

    class Meta(TeamListSerializer.Meta):
        fields = [
            *TeamListSerializer.Meta.fields,
            "members",
            "my_role",
            "invite_code",
            "invite_url",
        ]

    def _my_membership(self, team: Team) -> TeamMembership | None:
        user = self.context["request"].user
        return next((m for m in team.memberships.all() if m.user_id == user.pk), None)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_my_role(self, team: Team) -> str | None:
        membership = self._my_membership(team)
        return membership.role if membership else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_invite_code(self, team: Team) -> str | None:
        membership = self._my_membership(team)
        is_captain = membership is not None and membership.role == TeamMembership.Role.CAPTAIN
        return team.invite_code if is_captain else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_invite_url(self, team: Team) -> str | None:
        code = self.get_invite_code(team)
        return f"{settings.FRONTEND_URL}/invite/{code}" if code else None


class MyTeamSerializer(TeamListSerializer):
    my_role = serializers.SerializerMethodField()
    activity = serializers.SerializerMethodField()

    class Meta(TeamListSerializer.Meta):
        fields = [*TeamListSerializer.Meta.fields, "my_role", "activity"]

    def get_my_role(self, team: Team) -> str | None:
        user = self.context["request"].user
        return next((m.role for m in team.memberships.all() if m.user_id == user.pk), None)

    def get_activity(self, team: Team) -> str:
        return services.team_activity(team)


class TeamWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["name", "logo"]

    def validate_name(self, value: str) -> str:
        return value.strip()


class TeamInvitationSerializer(serializers.ModelSerializer):
    team = TeamMiniSerializer(read_only=True)
    invited_user = UserMiniSerializer(read_only=True)
    invited_by = UserMiniSerializer(read_only=True)

    class Meta:
        model = TeamInvitation
        fields = ["id", "team", "invited_user", "invited_by", "status", "created_at"]


class InvitePlayerSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=32)

    def validate_username(self, value: str) -> User:
        user = User.objects.filter(username__iexact=value, is_active=True).first()
        if user is None:
            raise serializers.ValidationError(_("No player with this game ID was found."), code="not_found")
        return user


class JoinTeamSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=16)
