import logging

from django.dispatch import receiver

from apps.notifications.signals import notifications_created

from . import services

logger = logging.getLogger(__name__)


@receiver(notifications_created, dispatch_uid="telegram_push_notifications")
def push_to_telegram(sender, notifications, **kwargs):
    # The notification is already saved; a bot hiccup must never surface as a failed request.
    try:
        services.push_notifications(notifications)
    except Exception:
        logger.exception("Pushing notifications to Telegram failed")
