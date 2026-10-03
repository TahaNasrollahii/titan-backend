import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import EncryptedTextField
from apps.core.models import TimeStampedModel
from apps.core.validators import IMAGE_VALIDATORS

username_validator = RegexValidator(
    r"^[A-Za-z0-9_.]{3,32}$",
    _("Username may contain 3-32 English letters, digits, dots and underscores."),
    code="invalid_username",
)


def random_avatar_seed() -> int:
    return random.randint(1, 60)


def xp_required_for_level(level: int) -> int:
    """XP needed to advance from ``level`` to ``level + 1``."""
    return 500 + level * 300


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone: str, password: str | None = None, **extra_fields):
        if not phone:
            raise ValueError("Users must have a phone number.")
        user = self.model(phone=phone, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()  # regular users authenticate with OTP only
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, password: str, **extra_fields):
        extra_fields.update(is_staff=True, is_superuser=True)
        return self.create_user(phone, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Presence(models.TextChoices):
        ONLINE = "online", _("Online")
        AWAY = "away", _("Away")
        IN_GAME = "in_game", _("In game")
        OFFLINE = "offline", _("Offline")

    phone = models.CharField(_("mobile number"), max_length=11, unique=True)
    username = models.CharField(
        _("game ID"), max_length=32, unique=True, null=True, blank=True, validators=[username_validator]
    )
    full_name = models.CharField(_("full name"), max_length=120, blank=True)
    email = models.EmailField(_("email"), blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, validators=IMAGE_VALIDATORS)
    avatar_seed = models.PositiveSmallIntegerField(default=random_avatar_seed)

    level = models.PositiveIntegerField(default=1)
    xp = models.PositiveIntegerField(default=0, help_text=_("Progress inside the current level."))
    points = models.PositiveIntegerField(default=0, db_index=True, help_text=_("Drives the rank tier."))
    favorite_game = models.ForeignKey(
        "catalog.Game", null=True, blank=True, on_delete=models.SET_NULL, related_name="fans"
    )

    presence_status = models.CharField(max_length=10, choices=Presence.choices, default=Presence.ONLINE)
    current_game = models.ForeignKey(
        "catalog.Game", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    last_seen = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["-date_joined"]

    def __str__(self) -> str:
        return self.username or self.phone

    @property
    def display_name(self) -> str:
        return self.username or self.full_name or self.phone

    @property
    def max_xp(self) -> int:
        return xp_required_for_level(self.level)

    @property
    def presence(self) -> str:
        if not self.last_seen:
            return self.Presence.OFFLINE
        if timezone.now() - self.last_seen > timedelta(seconds=settings.PRESENCE_TIMEOUT_SECONDS):
            return self.Presence.OFFLINE
        return self.presence_status


class OTPCode(models.Model):
    class Purpose(models.TextChoices):
        LOGIN = "login", _("Login")
        CHANGE_PHONE = "change_phone", _("Change phone")

    phone = models.CharField(max_length=11, db_index=True)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["phone", "purpose", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.phone} ({self.purpose})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at


class GameAccount(TimeStampedModel):
    """Third-party game/store credentials a user saves so staff can deliver top-ups."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_accounts")
    game = models.ForeignKey(
        "catalog.Game", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    title = models.CharField(_("title"), max_length=80)
    username = models.CharField(_("username or email"), max_length=150)
    password = EncryptedTextField(_("password"), blank=True)

    class Meta:
        ordering = ["created_at"]
        permissions = [("reveal_password", "Can reveal game account passwords")]

    def __str__(self) -> str:
        return f"{self.title} ({self.username})"


class Friendship(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACCEPTED = "accepted", _("Accepted")
        DECLINED = "declined", _("Declined")

    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="friendships_sent"
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="friendships_received"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["from_user", "to_user"], name="unique_friendship_direction"),
            models.CheckConstraint(condition=~Q(from_user=models.F("to_user")), name="no_self_friendship"),
        ]

    def __str__(self) -> str:
        return f"{self.from_user} -> {self.to_user} ({self.status})"


class RankTier(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=40)
    description = models.CharField(max_length=200, blank=True)
    min_points = models.PositiveIntegerField(unique=True)
    color_from = models.CharField(max_length=32)
    color_to = models.CharField(max_length=32)
    glow = models.CharField(max_length=40)
    ornament = models.CharField(max_length=20)

    class Meta:
        ordering = ["min_points"]

    def __str__(self) -> str:
        return self.name


class Badge(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True)
    icon = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class UserBadge(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_badges")
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="awards")
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["awarded_at"]
        constraints = [models.UniqueConstraint(fields=["user", "badge"], name="unique_user_badge")]

    def __str__(self) -> str:
        return f"{self.user} - {self.badge}"
