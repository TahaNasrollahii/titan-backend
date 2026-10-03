from django.contrib import admin

from .models import Announcement, ContactChannel, Promo, SiteSetting


@admin.register(Promo)
class PromoAdmin(admin.ModelAdmin):
    list_display = ["title", "placement", "is_active", "sort_order", "starts_at", "ends_at"]
    list_editable = ["is_active", "sort_order"]
    list_filter = ["placement", "is_active"]
    search_fields = ["title"]
    raw_id_fields = ["product", "tournament"]


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ["title", "is_active", "sort_order", "starts_at", "ends_at"]
    list_editable = ["is_active", "sort_order"]


@admin.register(ContactChannel)
class ContactChannelAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "is_primary", "is_online", "is_active", "sort_order"]
    list_editable = ["is_online", "is_active", "sort_order"]


@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    list_display = ["key", "value", "description"]
