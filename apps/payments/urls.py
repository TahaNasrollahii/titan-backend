from django.urls import path

from . import views

urlpatterns = [
    path("wallet/", views.WalletView.as_view(), name="wallet"),
    path("wallet/transactions/", views.WalletTransactionListView.as_view(), name="wallet-transactions"),
    path("wallet/topup/", views.WalletTopupView.as_view(), name="wallet-topup"),
    path("payments/callback/", views.PaymentCallbackView.as_view(), name="payment-callback"),
    path("payments/<int:pk>/", views.PaymentDetailView.as_view(), name="payment-detail"),
]
