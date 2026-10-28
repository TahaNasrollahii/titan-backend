from django.urls import path

from . import views

urlpatterns = [
    path("telegram/auth/", views.TelegramAuthView.as_view(), name="telegram-auth"),
    path("telegram/auth/contact/", views.TelegramContactView.as_view(), name="telegram-auth-contact"),
    path(
        "telegram/auth/code/request/", views.TelegramCodeRequestView.as_view(), name="telegram-code-request"
    ),
    path("telegram/auth/code/verify/", views.TelegramCodeVerifyView.as_view(), name="telegram-code-verify"),
    path("telegram/me/", views.TelegramAccountView.as_view(), name="telegram-me"),
    path("telegram/webhook/", views.TelegramWebhookView.as_view(), name="telegram-webhook"),
]
