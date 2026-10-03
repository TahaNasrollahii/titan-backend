from django.urls import path

from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("me/orders", views.OrderViewSet, basename="order")

urlpatterns = [
    path("cart/", views.CartView.as_view(), name="cart"),
    path("cart/items/", views.CartItemCreateView.as_view(), name="cart-items"),
    path("cart/items/<int:pk>/", views.CartItemDetailView.as_view(), name="cart-item-detail"),
    path("cart/merge/", views.CartMergeView.as_view(), name="cart-merge"),
    path("orders/checkout/", views.CheckoutView.as_view(), name="checkout"),
    *router.urls,
]
