from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.ranks import tier_for_points
from apps.accounts.serializers import PublicPlayerSerializer, RankTierSerializer, UserMiniSerializer
from apps.catalog.serializers import GameMiniSerializer
from apps.core.serializers import ImageUrlField
from apps.payments.models import PaymentMethod
from apps.teams.models import Team, TeamMembership
from apps.teams.serializers import TeamListSerializer, TeamMemberSerializer, TeamMiniSerializer

from .models import Match, PlayerStats, Registration, Season, TeamStats, Tournament, TournamentPrize


class SeasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Season
        fields = ["number", "name", "starts_at", "ends_at", "is_current"]


class PrizeSerializer(serializers.ModelSerializer):
    class Meta:
        model = TournamentPrize
        fields = ["place", "label", "amount", "points"]


class ParticipantSerializer(serializers.ModelSerializer):
    """A tournament entrant: either a team or a single player."""

    kind = serializers.SerializerMethodField()
    name = serializers.CharField(source="display_name", read_only=True)
    logo = serializers.SerializerMethodField()
    avatar_seed = serializers.SerializerMethodField()
    points = serializers.SerializerMethodField()
    rank = serializers.SerializerMethodField()
    team_id = serializers.IntegerField(read_only=True, allow_null=True)
    username = serializers.CharField(source="player.username", read_only=True, default=None)

    class Meta:
        model = Registration
        fields = [
            "id",
            "kind",
            "name",
            "logo",
            "avatar_seed",
            "team_id",
            "username",
            "points",
            "rank",
            "seed",
            "final_placement",
        ]

    def get_kind(self, registration: Registration) -> str:
        return "team" if registration.team_id else "player"

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_logo(self, registration: Registration) -> str | None:
        image = registration.team.logo if registration.team_id else registration.player.avatar
        if not image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(image.url) if request else image.url

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_avatar_seed(self, registration: Registration) -> int | None:
        return None if registration.team_id else registration.player.avatar_seed

    def get_points(self, registration: Registration) -> int:
        return registration.team.points if registration.team_id else registration.player.points

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_rank(self, registration: Registration) -> str | None:
        if registration.team_id:
            return None
        tier = tier_for_points(registration.player.points)
        return tier.slug if tier else None


class MatchSerializer(serializers.ModelSerializer):
    participant_a = ParticipantSerializer(read_only=True)
    participant_b = ParticipantSerializer(read_only=True)
    is_mine = serializers.SerializerMethodField()
    lobby_code = serializers.SerializerMethodField()

    class Meta:
        model = Match
        fields = [
            "id",
            "round",
            "position",
            "status",
            "scheduled_at",
            "best_of",
            "participant_a",
            "participant_b",
            "score_a",
            "score_b",
            "winner_id",
            "is_mine",
            "lobby_code",
        ]

    def _is_mine(self, match: Match) -> bool:
        mine = self.context.get("my_registration_ids", set())
        return bool(mine) and (match.participant_a_id in mine or match.participant_b_id in mine)

    def get_is_mine(self, match: Match) -> bool:
        return self._is_mine(match)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_lobby_code(self, match: Match) -> str | None:
        return (match.lobby_code or None) if self._is_mine(match) else None


class BracketRoundSerializer(serializers.Serializer):
    round = serializers.IntegerField()
    name = serializers.CharField()
    matches = MatchSerializer(many=True)


class TournamentMiniSerializer(serializers.ModelSerializer):
    game = GameMiniSerializer(read_only=True)
    cover_image = ImageUrlField()
    status = serializers.CharField(source="public_status", read_only=True)

    class Meta:
        model = Tournament
        fields = [
            "id",
            "slug",
            "title",
            "game",
            "cover_image",
            "status",
            "participant_type",
            "starts_at",
            "ends_at",
        ]


class TournamentListSerializer(serializers.ModelSerializer):
    game = GameMiniSerializer(read_only=True)
    cover_image = ImageUrlField()
    status = serializers.CharField(read_only=True)
    participant_type = serializers.CharField(read_only=True)
    participants_count = serializers.IntegerField(read_only=True)
    is_full = serializers.SerializerMethodField()
    is_free = serializers.BooleanField(read_only=True)
    entry_discount_percent = serializers.IntegerField(read_only=True)
    is_registered = serializers.BooleanField(read_only=True)
    featured_match = serializers.SerializerMethodField()

    class Meta:
        model = Tournament
        fields = [
            "id",
            "slug",
            "title",
            "game",
            "cover_image",
            "status",
            "is_full",
            "participant_type",
            "team_size",
            "format_label",
            "region",
            "prize_pool",
            "prize_currency",
            "entry_fee",
            "entry_fee_original",
            "entry_discount_percent",
            "is_free",
            "participants_count",
            "max_participants",
            "registration_closes_at",
            "starts_at",
            "ends_at",
            "is_featured",
            "viewer_count",
            "is_registered",
            "featured_match",
        ]

    def get_is_full(self, tournament: Tournament) -> bool:
        return tournament.participants_count >= tournament.max_participants

    @extend_schema_field(MatchSerializer(allow_null=True))
    def get_featured_match(self, tournament: Tournament):
        matches = getattr(tournament, "active_matches", None) or []
        return MatchSerializer(matches[0], context=self.context).data if matches else None


class RegistrationSerializer(serializers.ModelSerializer):
    team = TeamMiniSerializer(read_only=True)

    class Meta:
        model = Registration
        fields = [
            "id",
            "status",
            "team",
            "entry_fee_paid",
            "seed",
            "final_placement",
            "confirmed_at",
            "created_at",
        ]


class MyRegistrationSerializer(RegistrationSerializer):
    """The viewer's own entry: adds the lineup and whether the viewer may change or withdraw it."""

    members = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()

    class Meta(RegistrationSerializer.Meta):
        fields = [*RegistrationSerializer.Meta.fields, "members", "can_manage"]

    @extend_schema_field(UserMiniSerializer(many=True))
    def get_members(self, registration: Registration):
        users = [member.user for member in registration.members.select_related("user")]
        return UserMiniSerializer(users, many=True, context=self.context).data

    def get_can_manage(self, registration: Registration) -> bool:
        user = self.context["request"].user
        if registration.team_id:
            return registration.team.memberships.filter(user=user, role=TeamMembership.Role.CAPTAIN).exists()
        return registration.player_id == user.pk


class TournamentDetailSerializer(TournamentListSerializer):
    season = SeasonSerializer(read_only=True)
    prizes = PrizeSerializer(many=True, read_only=True)
    my_registration = serializers.SerializerMethodField()

    class Meta(TournamentListSerializer.Meta):
        fields = [
            *TournamentListSerializer.Meta.fields,
            "description",
            "rules",
            "prizes",
            "season",
            "format",
            "best_of",
            "registration_opens_at",
            "stream_url",
            "my_registration",
        ]

    @extend_schema_field(MyRegistrationSerializer(allow_null=True))
    def get_my_registration(self, tournament: Tournament):
        registration = self.context.get("my_registration")
        return MyRegistrationSerializer(registration, context=self.context).data if registration else None


class LineupSerializer(serializers.Serializer):
    members = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=50)


class RegisterSerializer(serializers.Serializer):
    team = serializers.PrimaryKeyRelatedField(queryset=Team.objects.active(), required=False, allow_null=True)
    members = serializers.ListField(
        child=serializers.IntegerField(min_value=1), max_length=50, required=False, default=list
    )
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices, required=False, allow_null=True)


class LineupCandidateSerializer(TeamMemberSerializer):
    """A team member offered for a lineup. ``registered_with`` names the entry they already play for."""

    registered_with = serializers.SerializerMethodField()

    class Meta(TeamMemberSerializer.Meta):
        fields = [*TeamMemberSerializer.Meta.fields, "registered_with"]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_registered_with(self, membership: TeamMembership) -> str | None:
        return self.context["registered_players"].get(membership.user_id)


class EligibleTeamSerializer(TeamListSerializer):
    members = LineupCandidateSerializer(source="memberships", many=True, read_only=True)

    class Meta(TeamListSerializer.Meta):
        fields = [*TeamListSerializer.Meta.fields, "members"]


class RegistrationResultSerializer(serializers.Serializer):
    registration = MyRegistrationSerializer()
    payment_url = serializers.URLField(allow_null=True)


class MyTournamentSerializer(RegistrationSerializer):
    tournament = TournamentMiniSerializer(read_only=True)
    current_stage = serializers.CharField(read_only=True, allow_null=True)

    class Meta(RegistrationSerializer.Meta):
        fields = [*RegistrationSerializer.Meta.fields, "tournament", "current_stage"]


class TeamTournamentSerializer(RegistrationSerializer):
    tournament = TournamentMiniSerializer(read_only=True)

    class Meta(RegistrationSerializer.Meta):
        fields = ["id", "status", "final_placement", "tournament"]


class MatchUpdateSerializer(serializers.Serializer):
    """Staff match editing. Sending both scores decides the match."""

    score_a = serializers.IntegerField(min_value=0, required=False)
    score_b = serializers.IntegerField(min_value=0, required=False)
    status = serializers.ChoiceField(choices=[Match.Status.SCHEDULED, Match.Status.LIVE], required=False)
    scheduled_at = serializers.DateTimeField(required=False)
    lobby_code = serializers.CharField(max_length=40, required=False, allow_blank=True)

    def validate(self, attrs):
        if ("score_a" in attrs) != ("score_b" in attrs):
            raise serializers.ValidationError(_("Send both scores to report a result."))
        return attrs


class UpcomingMatchSerializer(MatchSerializer):
    tournament = TournamentMiniSerializer(read_only=True)

    class Meta(MatchSerializer.Meta):
        fields = [*MatchSerializer.Meta.fields, "tournament"]


# ------------------------------------------------------------------ stats & leaderboards
STATS_FIELDS = [
    "matches",
    "wins",
    "losses",
    "win_rate",
    "points",
    "tournaments_played",
    "tournaments_won",
    "earnings_irt",
    "earnings_usd",
]


class StatsFieldsMixin(serializers.Serializer):
    matches = serializers.IntegerField()
    wins = serializers.IntegerField()
    losses = serializers.IntegerField()
    win_rate = serializers.FloatField()
    points = serializers.IntegerField()
    tournaments_played = serializers.IntegerField()
    tournaments_won = serializers.IntegerField()
    earnings_irt = serializers.IntegerField()
    earnings_usd = serializers.IntegerField()


class PlayerLeaderboardSerializer(StatsFieldsMixin, serializers.ModelSerializer):
    rank = serializers.IntegerField(read_only=True)
    player = UserMiniSerializer(source="user", read_only=True)
    game = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = PlayerStats
        fields = ["rank", "player", "game", *STATS_FIELDS]


class TeamLeaderboardSerializer(StatsFieldsMixin, serializers.ModelSerializer):
    rank = serializers.IntegerField(read_only=True)
    team = TeamMiniSerializer(read_only=True)
    game = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = TeamStats
        fields = ["rank", "team", "game", *STATS_FIELDS]


class PlayerGameStatsSerializer(StatsFieldsMixin, serializers.ModelSerializer):
    game = GameMiniSerializer(read_only=True)
    season = serializers.IntegerField(source="season.number", read_only=True)

    class Meta:
        model = PlayerStats
        fields = ["game", "season", *STATS_FIELDS]


class StatsTotalsSerializer(StatsFieldsMixin):
    rank = RankTierSerializer(allow_null=True)


class PlayerProfileSerializer(serializers.Serializer):
    player = PublicPlayerSerializer()
    totals = StatsTotalsSerializer()
    games = PlayerGameStatsSerializer(many=True)
    teams = TeamMiniSerializer(many=True)
