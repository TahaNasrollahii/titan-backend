from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Avg, Count

from apps.accounts.models import User
from apps.orders.models import Order, OrderItem

from .models import Product, ProductVariant, Review, WishlistItem


def sync_variant_summary(product_id: int) -> None:
    """Refresh the price/stock cache of a product from its options (no-op for fixed-price products).

    ``price``/``original_price`` mirror the cheapest option, ``price_max`` the most expensive one and
    ``stock`` the total (``None`` = unlimited if any option is unlimited).
    """
    variants = list(
        ProductVariant.objects.filter(product_id=product_id).order_by("price", "sort_order", "pk")
    )
    if not variants:
        Product.objects.filter(pk=product_id, has_variants=True).update(has_variants=False, price_max=None)
        return
    cheapest, priciest = variants[0], variants[-1]
    stocks = [variant.stock for variant in variants]
    Product.objects.filter(pk=product_id).update(
        has_variants=True,
        price=cheapest.price,
        original_price=cheapest.original_price,
        price_max=priciest.price,
        stock=None if None in stocks else sum(stocks),
    )


def has_purchased(user: User, product: Product) -> bool:
    return OrderItem.objects.filter(
        order__user=user, product=product, order__status__in=Order.PAID_STATUSES
    ).exists()


def recalculate_rating(product: Product) -> None:
    stats = product.reviews.filter(is_approved=True).aggregate(avg=Avg("rating"), count=Count("id"))
    average = Decimal(stats["avg"] or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    Product.objects.filter(pk=product.pk).update(rating_avg=average, review_count=stats["count"])


@transaction.atomic
def submit_review(
    user: User,
    product: Product,
    *,
    rating: int,
    author_name: str,
    author_email: str,
    comment: str = "",
) -> Review:
    """Create or update the user's single review of ``product`` and refresh the aggregates."""
    review, _ = Review.objects.update_or_create(
        product=product,
        user=user,
        defaults={
            "rating": rating,
            "comment": comment,
            "author_name": author_name,
            "author_email": author_email,
            "verified_purchase": has_purchased(user, product),
        },
    )
    recalculate_rating(product)
    return review


@transaction.atomic
def delete_review(review: Review) -> None:
    product = review.product
    review.delete()
    recalculate_rating(product)


def add_to_wishlist(user: User, product: Product) -> WishlistItem:
    item, _ = WishlistItem.objects.get_or_create(user=user, product=product)
    return item


def remove_from_wishlist(user: User, product: Product) -> None:
    WishlistItem.objects.filter(user=user, product=product).delete()
