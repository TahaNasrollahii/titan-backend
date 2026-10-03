from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "kind", "is_read", "created_at"]
    list_filter = ["kind", "is_read"]
    search_fields = ["title", "user__phone", "user__username"]
    raw_id_fields = ["user"]
