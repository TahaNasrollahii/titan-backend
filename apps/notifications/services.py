from collections.abc import Iterable

from apps.accounts.models import User

from .models import Notification

Kind = Notification.Kind


def notify(
    user: User,
    *,
    kind: str = Kind.SYSTEM,
    title: str,
    body: str = "",
    icon: str = "",
    data: dict | None = None,
) -> Notification:
    from django.utils.translation import override
    with override("fa"):
        return Notification.objects.create(
            user=user, kind=kind, title=str(title), body=str(body), icon=icon, data=data or {}
        )


def notify_many(users: Iterable[User], **kwargs) -> list[Notification]:
    from django.utils.translation import override
    with override("fa"):
        data = kwargs.pop("data", None) or {}
        title, body = str(kwargs.pop("title")), str(kwargs.pop("body", ""))
        return Notification.objects.bulk_create(
            [Notification(user=user, title=title, body=body, data=data, **kwargs) for user in users]
        )


def mark_read(user: User, notification_id: int) -> Notification:
    notification = Notification.objects.get(user=user, pk=notification_id)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    return notification


def mark_all_read(user: User) -> int:
    return Notification.objects.filter(user=user, is_read=False).update(is_read=True)


def resolve_action(user: User, *, kind: str, key: str, value: int) -> None:
    """Mark notifications that carried an action (e.g. an invitation) as read once it is handled."""
    Notification.objects.filter(user=user, kind=kind, **{f"data__{key}": value}).update(is_read=True)
