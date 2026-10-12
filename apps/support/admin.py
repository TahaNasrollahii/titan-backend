"""Back-office views of live chat. Agents answer from the storefront desk (``/desk``); the admin keeps the
history, agent profiles and canned replies."""

from django.conf import settings
from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateFilter
from unfold.decorators import display

from apps.core.admin import INFO, SUCCESS, WARNING

from .models import CannedReply, Conversation, Message, SupportAgent


class MessageInline(TabularInline):
    model = Message
    fields = ["created_at", "kind", "author", "body", "image"]
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = False
    ordering = ["id"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Conversation)
class ConversationAdmin(ModelAdmin):
    list_display = [
        "ticket_label",
        "full_name",
        "phone",
        "status_label",
        "assignee",
        "rating",
        "last_message_at",
    ]
    list_filter = [
        ("status", ChoicesDropdownFilter),
        ("rating", ChoicesDropdownFilter),
        ("created_at", RangeDateFilter),
    ]
    list_filter_submit = True
    list_select_related = ["assignee", "user"]
    search_fields = ["first_name", "last_name", "phone", "user__username"]
    readonly_fields = [
        "ticket_label",
        "first_name",
        "last_name",
        "phone",
        "user",
        "status",
        "assignee",
        "page_url",
        "rating",
        "rating_comment",
        "created_at",
        "last_message_at",
        "closed_at",
        "alerted_at",
        "visitor_notified_at",
        "desk_link",
    ]
    fieldsets = [
        (None, {"fields": ["ticket_label", "desk_link", ("first_name", "last_name"), "phone", "user"]}),
        (_("Status"), {"fields": ["status", "assignee", ("created_at", "last_message_at", "closed_at")]}),
        (_("Feedback"), {"fields": ["rating", "rating_comment"]}),
        (
            _("Details"),
            {"fields": ["page_url", "alerted_at", "visitor_notified_at"], "classes": ["collapse"]},
        ),
    ]
    inlines = [MessageInline]

    def has_add_permission(self, request):
        return False

    @display(description=_("Ticket"), ordering="id")
    def ticket_label(self, obj: Conversation):
        return obj.ticket

    @display(description=_("Status"), label={"open": WARNING, "closed": SUCCESS}, ordering="status")
    def status_label(self, obj: Conversation):
        return obj.status, obj.get_status_display()

    @display(description=_("Desk"))
    def desk_link(self, obj: Conversation):
        url = f"{settings.FRONTEND_URL}/desk?c={obj.pk}"
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, _("Open in the desk"))


@admin.register(SupportAgent)
class SupportAgentAdmin(ModelAdmin):
    list_display = ["user", "name", "online_label", "sms_alerts", "last_seen"]
    list_select_related = ["user"]
    autocomplete_fields = ["user"]
    readonly_fields = ["last_seen", "last_alerted_at"]
    fields = ["user", "display_name", "is_available", "sms_alerts", "last_seen", "last_alerted_at"]

    @display(description=_("Online"), label={True: SUCCESS, False: INFO})
    def online_label(self, obj: SupportAgent):
        return (True, _("Online")) if obj.is_online else (False, _("Offline"))


@admin.register(CannedReply)
class CannedReplyAdmin(ModelAdmin):
    list_display = ["shortcut_label", "title", "position", "is_active"]
    list_editable = ["position", "is_active"]
    search_fields = ["shortcut", "title", "body"]
    fields = ["shortcut", "title", "body", "position", "is_active"]

    @display(description=_("shortcut"), ordering="shortcut")
    def shortcut_label(self, obj: CannedReply):
        return f"/{obj.shortcut}"
