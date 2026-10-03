from django.conf import settings
from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.serializers import PresenceUserSerializer, UserMiniSerializer
from apps.catalog.models import Game
from apps.catalog.serializers import GameMiniSerializer
from apps.core.serializers import ImageUrlField

from . import services
from .models import Team, TeamInvitation, TeamMembership


class TeamMemberSerializer(serializers.ModelSerializer):
    user = PresenceUserSerializer(read_only=True)

    class Meta:
        model = TeamMembership
        fields = ["user", "role", "joined_at"]


class TeamMiniSerializer(serializers.ModelSerializer):
    logo = ImageUrlField()

    class Meta:
        model = Team
        fields = ["id", "name", "tag", "logo"]


class TeamListSerializer(serializers.ModelSerializer):
    logo = ImageUrlField()
    game = GameMiniSerializer(read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    win_rate = serializers.FloatField(read_only=True)

    class Meta:
        model = Team
        fields = [
            "id",
            "name",
            "tag",
            "logo",
            "game",
            "region",
            "member_count",
            "max_members",
            "matches_played",
            "wins",
            "losses",
            "win_rate",
            "points",
            "created_at",
        ]


class TeamDetailSerializer(TeamListSerializer):
    members = TeamMemberSerializer(source="memberships", many=True, read_only=True)
    my_role = serializers.SerializerMethodField()
    invite_code = serializers.SerializerMethodField()
    invite_url = serializers.SerializerMethodField()

    class Meta(TeamListSerializer.Meta):
        fields = [
            *TeamListSerializer.Meta.fields,
            "description",
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
    game = serializers.SlugRelatedField(slug_field="slug", queryset=Game.objects.filter(is_active=True))
    max_members = serializers.IntegerField(min_value=2, max_value=20, required=False)

    class Meta:
        model = Team
        fields = ["name", "tag", "description", "logo", "game", "region", "max_members"]

    def validate_tag(self, value: str) -> str:
        value = value.strip().upper()
        if not value.isalnum():
            raise serializers.ValidationError(_("The tag may only contain letters and digits."))
        return value


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
