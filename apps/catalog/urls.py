from django.urls import path

from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("games", views.GameViewSet, basename="game")
router.register("products", views.ProductViewSet, basename="product")
router.register(r"products/(?P<product_slug>[-\w]+)/reviews", views.ProductReviewViewSet, basename="review")
router.register("me/wishlist", views.WishlistViewSet, basename="wishlist")

urlpatterns = [
    path("platforms/", views.PlatformListView.as_view(), name="platform-list"),
    path("product-categories/", views.ProductCategoryListView.as_view(), name="product-category-list"),
    *router.urls,
]
