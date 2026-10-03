from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.catalog.models import Game
from apps.core.serializers import ImageUrlField
from apps.core.utils import normalize_digits, normalize_phone

from .models import Badge, Friendship, GameAccount, RankTier, User
from .ranks import tier_for_points


# ------------------------------------------------------------------ reference data
class RankTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = RankTier
        fields = ["slug", "name", "description", "min_points", "color_from", "color_to", "glow", "ornament"]


class BadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Badge
        fields = ["slug", "name", "description", "icon"]


class GameRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Game
        fields = ["slug", "title", "title_en"]


class RankFieldMixin(serializers.Serializer):
    rank = serializers.SerializerMethodField()

    @extend_schema_field(RankTierSerializer(allow_null=True))
    def get_rank(self, user: User):
        tier = tier_for_points(user.points)
        return RankTierSerializer(tier).data if tier else None


# ------------------------------------------------------------------ users
class UserMiniSerializer(serializers.ModelSerializer):
    """Compact public representation used inside other resources (members, reviews, brackets)."""

    avatar = ImageUrlField()
    rank = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "display_name", "avatar", "avatar_seed", "level", "points", "rank"]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_rank(self, user: User) -> str | None:
        tier = tier_for_points(user.points)
        return tier.slug if tier else None


class PresenceUserSerializer(UserMiniSerializer):
    presence = serializers.CharField(read_only=True)
    current_game = GameRefSerializer(read_only=True)

    class Meta(UserMiniSerializer.Meta):
        fields = [*UserMiniSerializer.Meta.fields, "presence", "current_game"]


class PublicPlayerSerializer(RankFieldMixin, serializers.ModelSerializer):
    avatar = ImageUrlField()
    favorite_game = GameRefSerializer(read_only=True)
    badges = serializers.SerializerMethodField()
    max_xp = serializers.IntegerField(read_only=True)
    presence = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "display_name",
            "avatar",
            "avatar_seed",
            "level",
            "xp",
            "max_xp",
            "points",
            "rank",
            "favorite_game",
            "badges",
            "presence",
            "date_joined",
        ]

    @extend_schema_field(BadgeSerializer(many=True))
    def get_badges(self, user: User):
        return BadgeSerializer([award.badge for award in user.user_badges.all()], many=True).data


class MeSerializer(PublicPlayerSerializer):
    favorite_game = serializers.SlugRelatedField(
        slug_field="slug", queryset=Game.objects.all(), allow_null=True, required=False
    )
    favorite_game_detail = GameRefSerializer(source="favorite_game", read_only=True)

    class Meta(PublicPlayerSerializer.Meta):
        fields = [
            *PublicPlayerSerializer.Meta.fields,
            "phone",
            "full_name",
            "email",
            "favorite_game_detail",
            "is_staff",
        ]
        read_only_fields = [
            "id",
            "phone",
            "avatar",
            "level",
            "xp",
            "points",
            "date_joined",
            "is_staff",
        ]
        extra_kwargs = {"username": {"required": False, "allow_null": False}}

    def validate_username(self, value: str) -> str:
        queryset = User.objects.filter(username__iexact=value).exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError(_("This game ID is already taken."), code="username_taken")
        return value


class AvatarSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["avatar"]
        extra_kwargs = {"avatar": {"required": True, "allow_null": False}}


# ------------------------------------------------------------------ auth
class PhoneSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)

    def validate_phone(self, value: str) -> str:
        return normalize_phone(value)


class OTPVerifySerializer(PhoneSerializer):
    code = serializers.CharField(max_length=10)

    def validate_code(self, value: str) -> str:
        return normalize_digits(value).strip()


class OTPRequestResponseSerializer(serializers.Serializer):
    phone = serializers.CharField()
    expires_in = serializers.IntegerField()
    resend_in = serializers.IntegerField()


class AuthTokensSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    is_new_user = serializers.BooleanField()
    user = MeSerializer()


class RefreshTokenSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class HeartbeatSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[User.Presence.ONLINE, User.Presence.AWAY, User.Presence.IN_GAME],
        default=User.Presence.ONLINE,
    )
    game = serializers.SlugRelatedField(
        slug_field="slug", queryset=Game.objects.all(), required=False, allow_null=True
    )


# ------------------------------------------------------------------ game accounts
class GameAccountSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=False, allow_blank=True, style={"input_type": "password"}, max_length=256
    )
    has_password = serializers.SerializerMethodField()
    game = serializers.SlugRelatedField(
        slug_field="slug", queryset=Game.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = GameAccount
        fields = ["id", "title", "username", "password", "has_password", "game", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_has_password(self, account: GameAccount) -> bool:
        return bool(account.password)

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": _("This field is required.")})
        if self.instance is not None and not attrs.get("password"):
            attrs.pop("password", None)  # blank on update means "keep the current password"
        return attrs


# ------------------------------------------------------------------ friends
class FriendRequestSerializer(serializers.ModelSerializer):
    from_user = UserMiniSerializer(read_only=True)
    to_user = UserMiniSerializer(read_only=True)

    class Meta:
        model = Friendship
        fields = ["id", "from_user", "to_user", "status", "created_at"]


class AddFriendSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=32)

    def validate_username(self, value: str) -> User:
        user = User.objects.filter(username__iexact=value, is_active=True).first()
        if user is None:
            raise serializers.ValidationError(_("No player with this game ID was found."), code="not_found")
        return user
