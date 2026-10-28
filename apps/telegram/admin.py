from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import TelegramAccount


@admin.register(TelegramAccount)
class TelegramAccountAdmin(ModelAdmin):
    list_display = [
        "__str__",
        "user",
        "full_name",
        "reachable_label",
        "notifications_enabled",
        "last_opened_at",
    ]
    list_filter = ["notifications_enabled", "can_message", "is_premium"]
    search_fields = ["telegram_id", "username", "first_name", "last_name", "user__phone", "user__username"]
    readonly_fields = [
        "user",
        "telegram_id",
        "username",
        "first_name",
        "last_name",
        "language_code",
        "photo_url",
        "is_premium",
        "can_message",
        "last_opened_at",
        "created_at",
        "updated_at",
    ]

    @display(description=_("Reachable"), boolean=True)
    def reachable_label(self, obj):
        return obj.can_message

    def has_add_permission(self, request):
        return False
