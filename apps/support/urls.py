from django.urls import path

from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("support/desk/canned-replies", views.CannedReplyViewSet, basename="support-canned-reply")

urlpatterns = [
    path("support/status/", views.StatusView.as_view(), name="support-status"),
    # visitor (X-Support-Token)
    path("support/chat/start/", views.StartView.as_view(), name="support-start"),
    path("support/chat/sync/", views.SyncView.as_view(), name="support-sync"),
    path("support/chat/messages/", views.VisitorMessageView.as_view(), name="support-messages"),
    path("support/chat/read/", views.VisitorReadView.as_view(), name="support-read"),
    path("support/chat/typing/", views.VisitorTypingView.as_view(), name="support-typing"),
    path("support/chat/close/", views.VisitorCloseView.as_view(), name="support-close"),
    path("support/chat/rate/", views.VisitorRateView.as_view(), name="support-rate"),
    # desk (staff)
    path("support/desk/sync/", views.DeskSyncView.as_view(), name="desk-sync"),
    path("support/desk/me/", views.DeskMeView.as_view(), name="desk-me"),
    path("support/desk/agents/", views.DeskAgentsView.as_view(), name="desk-agents"),
    path("support/desk/conversations/", views.DeskConversationListView.as_view(), name="desk-conversations"),
    path(
        "support/desk/conversations/<int:pk>/messages/", views.DeskMessageView.as_view(), name="desk-messages"
    ),
    path("support/desk/conversations/<int:pk>/assign/", views.DeskAssignView.as_view(), name="desk-assign"),
    path("support/desk/conversations/<int:pk>/close/", views.DeskCloseView.as_view(), name="desk-close"),
    path("support/desk/conversations/<int:pk>/read/", views.DeskReadView.as_view(), name="desk-read"),
    path("support/desk/conversations/<int:pk>/typing/", views.DeskTypingView.as_view(), name="desk-typing"),
    *router.urls,
]
