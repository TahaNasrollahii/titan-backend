from django import forms
from django.contrib import admin
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import BooleanRadioFilter, ChoicesDropdownFilter, RangeDateFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminTextareaWidget, UnfoldAdminTextInputWidget

from apps.accounts.models import User
from apps.core.admin import INFO, SUCCESS, WARNING, redirect_after_action

from . import services
from .models import Notification

KIND_LABELS = {
    Notification.Kind.SYSTEM: INFO,
    Notification.Kind.TOURNAMENT: WARNING,
    Notification.Kind.ORDER: SUCCESS,
    Notification.Kind.PAYMENT: SUCCESS,
}


class BroadcastForm(BaseDialogForm):
    title = forms.CharField(label=_("Title"), max_length=120, widget=UnfoldAdminTextInputWidget)
    body = forms.CharField(label=_("Message"), required=False, widget=UnfoldAdminTextareaWidget)


@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    list_display = ["title", "user", "kind_label", "read_label", "created_at"]
    list_filter = [
        ("kind", ChoicesDropdownFilter),
        ("is_read", BooleanRadioFilter),
        ("created_at", RangeDateFilter),
    ]
    list_filter_submit = True
    list_select_related = ["user"]
    search_fields = ["title", "user__phone", "user__username"]
    autocomplete_fields = ["user"]
    readonly_fields = ["created_at"]
    fields = ["user", ("kind", "icon"), "title", "body", "data", "is_read", "created_at"]
    actions = ["mark_read"]
    actions_list = ["broadcast"]

    @display(description=_("kind"), label=KIND_LABELS, ordering="kind")
    def kind_label(self, obj: Notification):
        return obj.kind, obj.get_kind_display()

    @display(description=_("read"), label={True: SUCCESS, False: WARNING}, ordering="is_read")
    def read_label(self, obj: Notification):
        return (True, _("Read")) if obj.is_read else (False, _("Unread"))

    @action(description=_("Mark as read"), icon="done_all", permissions=["change"])
    def mark_read(self, request, queryset):
        updated = queryset.update(is_read=True)
        self.message_user(request, _("%(count)d notification(s) marked as read.") % {"count": updated})

    @action(
        description=_("Broadcast to all players"),
        icon="campaign",
        variant=ActionVariant.PRIMARY,
        permissions=["add"],
        dialog={
            "title": _("Broadcast a notification"),
            "description": _("Sends a system notification to every active player."),
            "form_class": BroadcastForm,
            "form_submit_text": _("Send"),
        },
    )
    def broadcast(self, request, form):
        sent = services.notify_many(
            User.objects.filter(is_active=True),
            kind=Notification.Kind.SYSTEM,
            icon="campaign",
            title=form.cleaned_data["title"],
            body=form.cleaned_data["body"],
        )
        self.message_user(request, _("Sent to %(count)d player(s).") % {"count": len(sent)})
        return redirect_after_action(request, reverse("admin:notifications_notification_changelist"))
