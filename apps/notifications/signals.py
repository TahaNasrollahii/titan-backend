"""Hooks for other apps. Kept here so notifications never import the channels that deliver them."""

from django.dispatch import Signal

# Sent after commit with ``notifications``: the list of newly created Notification rows.
notifications_created = Signal()
