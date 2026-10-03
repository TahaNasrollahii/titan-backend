from django.db.models import BooleanField, Count, Exists, OuterRef, Prefetch, Q, QuerySet, Value

from apps.accounts.models import User

from .models import Game, Product, ProductVariant, WishlistItem


def games_with_counts() -> QuerySet[Game]:
    return Game.objects.filter(is_active=True).annotate(
        product_count=Count("products", filter=Q(products__is_active=True), distinct=True),
        tournament_count=Count("tournaments", distinct=True),
        player_count=Count("fans", distinct=True),
    )


def with_wishlist_flag(queryset: QuerySet[Product], user: User | None) -> QuerySet[Product]:
    if user is None or not user.is_authenticated:
        return queryset.annotate(is_wishlisted=Value(False, output_field=BooleanField()))
    return queryset.annotate(
        is_wishlisted=Exists(WishlistItem.objects.filter(user=user, product=OuterRef("pk")))
    )


def product_list(user: User | None) -> QuerySet[Product]:
    queryset = Product.objects.active().select_related("game", "category").prefetch_related("platforms")
    return with_wishlist_flag(queryset, user)


def product_detail(user: User | None) -> QuerySet[Product]:
    return product_list(user).prefetch_related(
        "gallery", Prefetch("variants", queryset=ProductVariant.objects.order_by("sort_order", "price"))
    )


def related_products(product: Product, user: User | None, limit: int = 8) -> QuerySet[Product]:
    related = Q(category=product.category)
    if product.game_id:
        related |= Q(game=product.game)
    return product_list(user).filter(related).exclude(pk=product.pk).order_by("-popularity")[:limit]
