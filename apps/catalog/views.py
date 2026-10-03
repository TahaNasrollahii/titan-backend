from django.shortcuts import get_object_or_404

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from . import selectors, services
from .filters import ProductFilter
from .models import Platform, Product, ProductCategory, Review, WishlistItem
from .serializers import (
    GameSerializer,
    PlatformSerializer,
    ProductCategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
    ReviewSerializer,
    WishlistAddSerializer,
    WishlistItemSerializer,
)


@extend_schema(tags=["catalog"])
class GameViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = GameSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"
    pagination_class = None
    filterset_fields = ["kind", "is_featured"]
    search_fields = ["title", "title_en", "genre"]
    ordering_fields: list = []

    def get_queryset(self):
        return selectors.games_with_counts()


@extend_schema(tags=["catalog"])
class PlatformListView(generics.ListAPIView):
    queryset = Platform.objects.all()
    serializer_class = PlatformSerializer
    permission_classes = [AllowAny]
    pagination_class = None
    filter_backends: list = []


@extend_schema(tags=["catalog"])
class ProductCategoryListView(generics.ListAPIView):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer
    permission_classes = [AllowAny]
    pagination_class = None
    filter_backends: list = []


@extend_schema(tags=["catalog"])
class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    """Store catalogue.

    Sort with ``ordering``: ``-popularity`` (default), ``price``, ``-price`` or ``-created_at``.
    """

    permission_classes = [AllowAny]
    lookup_field = "slug"
    filterset_class = ProductFilter
    search_fields = ["title", "subtitle", "vendor", "game__title", "game__title_en"]
    ordering_fields = ["popularity", "price", "created_at", "rating_avg"]
    ordering = ["-popularity", "-created_at"]

    def get_queryset(self):
        if self.action == "retrieve":
            return selectors.product_detail(self.request.user)
        return selectors.product_list(self.request.user)

    def get_serializer_class(self):
        return ProductDetailSerializer if self.action == "retrieve" else ProductListSerializer

    @extend_schema(responses=ProductListSerializer(many=True))
    @action(detail=True, methods=["get"], filter_backends=[], pagination_class=None)
    def related(self, request, slug=None):
        product = get_object_or_404(Product.objects.active(), slug=slug)
        products = selectors.related_products(product, request.user)
        return Response(ProductListSerializer(products, many=True, context={"request": request}).data)


@extend_schema(tags=["catalog"])
class ProductReviewViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Reviews of a product. Posting again updates the caller's existing review."""

    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    filter_backends: list = []

    def get_product(self) -> Product:
        return get_object_or_404(Product.objects.active(), slug=self.kwargs["product_slug"])

    def get_queryset(self):
        return Review.objects.filter(
            product__slug=self.kwargs["product_slug"], is_approved=True
        ).select_related("user")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = services.submit_review(request.user, self.get_product(), **serializer.validated_data)
        return Response(self.get_serializer(review).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={204: None})
    @action(detail=False, methods=["delete"], url_path="mine")
    def delete_mine(self, request, product_slug=None):
        review = get_object_or_404(Review, product__slug=product_slug, user=request.user)
        services.delete_review(review)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["catalog"])
class WishlistViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = WishlistItemSerializer
    filter_backends: list = []
    lookup_field = "product_slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        return (
            WishlistItem.objects.filter(user=self.request.user, product__is_active=True)
            .select_related("product__game", "product__category")
            .prefetch_related("product__platforms")
        )

    @extend_schema(request=WishlistAddSerializer, responses={201: WishlistItemSerializer})
    def create(self, request):
        serializer = WishlistAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = services.add_to_wishlist(request.user, serializer.validated_data["product"])
        return Response(self.get_serializer(item).data, status=status.HTTP_201_CREATED)

    @extend_schema(parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH)], responses={204: None})
    def destroy(self, request, slug=None):
        product = get_object_or_404(Product, slug=slug)
        services.remove_from_wishlist(request.user, product)
        return Response(status=status.HTTP_204_NO_CONTENT)
