from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Notification(models.Model):
    class Kind(models.TextChoices):
        SYSTEM = "system", _("System")
        TOURNAMENT = "tournament", _("Tournament")
        TEAM_INVITE = "team_invite", _("Team invitation")
        TEAM = "team", _("Team")
        FRIEND_REQUEST = "friend_request", _("Friend request")
        ORDER = "order", _("Order")
        PAYMENT = "payment", _("Payment")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SYSTEM)
    title = models.CharField(max_length=120)
    body = models.TextField(blank=True)
    icon = models.CharField(max_length=40, blank=True)
    data = models.JSONField(default=dict, blank=True, help_text=_("Action payload consumed by the frontend."))
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_read", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.user}: {self.title}"
