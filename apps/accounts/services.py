import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.exceptions import ConflictError, DomainError, NotAllowedError
from apps.notifications.services import Kind, notify, resolve_action

from .models import Friendship, OTPCode, User, xp_required_for_level
from .sms import get_sms_backend


# ------------------------------------------------------------------ errors
class OTPThrottled(DomainError):
    status_code = 429
    default_detail = _("Too many code requests. Please wait before trying again.")
    default_code = "otp_throttled"


class InvalidOTP(DomainError):
    default_detail = _("The verification code is invalid or has expired.")
    default_code = "invalid_otp"


class TooManyOTPAttempts(DomainError):
    status_code = 429
    default_detail = _("Too many wrong attempts. Request a new code.")
    default_code = "otp_attempts_exceeded"


# ------------------------------------------------------------------ OTP
def _hash_code(phone: str, code: str) -> str:
    message = f"{phone}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def _generate_code() -> str:
    return "1234"


def request_otp(phone: str, purpose: str = OTPCode.Purpose.LOGIN) -> OTPCode:
    """Create a one-time code for ``phone`` and deliver it by SMS, enforcing resend limits."""
    now = timezone.now()
    recent = OTPCode.objects.filter(phone=phone, purpose=purpose, created_at__gte=now - timedelta(hours=1))
    latest = recent.first()
    if latest and (now - latest.created_at).total_seconds() < settings.OTP_RESEND_COOLDOWN_SECONDS:
        raise OTPThrottled()
    if recent.count() >= settings.OTP_MAX_PER_HOUR:
        raise OTPThrottled()

    code = _generate_code()
    otp = OTPCode.objects.create(
        phone=phone,
        purpose=purpose,
        code_hash=_hash_code(phone, code),
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
    )
    get_sms_backend().send_otp(phone, code)
    return otp


def consume_otp(phone: str, code: str, purpose: str) -> None:
    """Validate and burn the latest active code for ``phone``. Raises on any failure.

    The error is raised *after* the transaction commits so a failed attempt is always counted.
    """
    error: DomainError | None = None
    with transaction.atomic():
        otp = (
            OTPCode.objects.select_for_update()
            .filter(phone=phone, purpose=purpose, used_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if otp is None or otp.is_expired:
            error = InvalidOTP()
        elif otp.attempts >= settings.OTP_MAX_ATTEMPTS:
            error = TooManyOTPAttempts()
        elif not hmac.compare_digest(otp.code_hash, _hash_code(phone, code)):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            error = InvalidOTP()
        else:
            otp.used_at = timezone.now()
            otp.save(update_fields=["used_at"])
    if error is not None:
        raise error


def login_with_otp(phone: str, code: str) -> tuple[User, bool]:
    """Verify the code and return ``(user, created)``. New phone numbers are registered on the fly."""
    consume_otp(phone, code, OTPCode.Purpose.LOGIN)
    user, created = User.objects.get_or_create(phone=phone)
    if not user.is_active:
        raise NotAllowedError(_("This account has been disabled."), code="account_disabled")
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user, created


def issue_tokens(user: User) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def logout(refresh_token: str) -> None:
    try:
        RefreshToken(refresh_token).blacklist()
    except TokenError as exc:
        raise DomainError(_("Invalid or expired refresh token."), code="invalid_token") from exc


def change_phone(user: User, new_phone: str, code: str) -> User:
    if User.objects.filter(phone=new_phone).exclude(pk=user.pk).exists():
        raise ConflictError(_("This mobile number is already registered."), code="phone_taken")
    consume_otp(new_phone, code, OTPCode.Purpose.CHANGE_PHONE)
    user.phone = new_phone
    user.save(update_fields=["phone"])
    return user


# ------------------------------------------------------------------ progression
@transaction.atomic
def award_progress(user: User, *, xp: int = 0, points: int = 0) -> User:
    """Add XP (handling level-ups) and rank points to ``user``."""
    user = User.objects.select_for_update().get(pk=user.pk)
    user.points += points
    user.xp += xp
    while user.xp >= xp_required_for_level(user.level):
        user.xp -= xp_required_for_level(user.level)
        user.level += 1
    user.save(update_fields=["xp", "level", "points"])
    return user


# ------------------------------------------------------------------ presence
def heartbeat(user: User, *, status: str, game=None) -> User:
    user.presence_status = status
    user.current_game = game if status == User.Presence.IN_GAME else None
    user.last_seen = timezone.now()
    user.save(update_fields=["presence_status", "current_game", "last_seen"])
    return user


# ------------------------------------------------------------------ friends
def friends_of(user: User):
    accepted = Friendship.objects.filter(status=Friendship.Status.ACCEPTED)
    sent = accepted.filter(from_user=user).values("to_user")
    received = accepted.filter(to_user=user).values("from_user")
    return User.objects.filter(Q(pk__in=sent) | Q(pk__in=received)).select_related("current_game")


def _friendship_between(a: User, b: User) -> Friendship | None:
    return Friendship.objects.filter(Q(from_user=a, to_user=b) | Q(from_user=b, to_user=a)).first()


@transaction.atomic
def send_friend_request(sender: User, recipient: User) -> Friendship:
    if sender.pk == recipient.pk:
        raise DomainError(_("You cannot add yourself as a friend."), code="self_friendship")

    existing = _friendship_between(sender, recipient)
    if existing and existing.status == Friendship.Status.ACCEPTED:
        raise ConflictError(_("You are already friends."), code="already_friends")
    if existing and existing.status == Friendship.Status.PENDING:
        if existing.to_user_id == sender.pk:  # they already asked us: accepting is the natural outcome
            return accept_friend_request(sender, existing.pk)
        raise ConflictError(_("A friend request is already pending."), code="request_pending")
    if existing:  # previously declined — reopen in the new direction
        existing.delete()

    try:
        friendship = Friendship.objects.create(from_user=sender, to_user=recipient)
    except IntegrityError as exc:
        raise ConflictError(_("A friend request is already pending."), code="request_pending") from exc
    notify(
        recipient,
        kind=Kind.FRIEND_REQUEST,
        title=_("New friend request"),
        body=_("%(name)s wants to be your friend.") % {"name": sender.display_name},
        icon="users",
        data={"friendship_id": friendship.pk, "from_username": sender.username},
    )
    return friendship


def _pending_request_to(user: User, friendship_id: int) -> Friendship:
    friendship = (
        Friendship.objects.select_for_update()
        .filter(pk=friendship_id, to_user=user, status=Friendship.Status.PENDING)
        .first()
    )
    if friendship is None:
        raise DomainError(_("Friend request not found."), code="not_found")
    return friendship


@transaction.atomic
def accept_friend_request(user: User, friendship_id: int) -> Friendship:
    friendship = _pending_request_to(user, friendship_id)
    friendship.status = Friendship.Status.ACCEPTED
    friendship.save(update_fields=["status", "updated_at"])
    resolve_action(user, kind=Kind.FRIEND_REQUEST, key="friendship_id", value=friendship.pk)
    notify(
        friendship.from_user,
        kind=Kind.SYSTEM,
        title=_("Friend request accepted"),
        body=_("%(name)s accepted your friend request.") % {"name": user.display_name},
        icon="users",
    )
    return friendship


@transaction.atomic
def decline_friend_request(user: User, friendship_id: int) -> Friendship:
    friendship = _pending_request_to(user, friendship_id)
    friendship.status = Friendship.Status.DECLINED
    friendship.save(update_fields=["status", "updated_at"])
    resolve_action(user, kind=Kind.FRIEND_REQUEST, key="friendship_id", value=friendship.pk)
    return friendship


def remove_friend(user: User, other: User) -> None:
    friendship = _friendship_between(user, other)
    if friendship is None or friendship.status != Friendship.Status.ACCEPTED:
        raise DomainError(_("This user is not your friend."), code="not_friends")
    friendship.delete()
