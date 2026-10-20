import json
import logging
import threading
import urllib.request

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.content.models import Promo

from .models import Game, Product, ProductCategory, ProductVariant
from .services import sync_variant_summary

logger = logging.getLogger(__name__)
_local = threading.local()


def _schedule_frontend_revalidation(tags: list[str], paths: list[str] | None = None) -> None:
    """Batch and trigger Next.js on-demand cache revalidation once the current DB transaction commits."""
    if getattr(settings, "IS_TESTING", False):
        return

    pending_tags: set[str] | None = getattr(_local, "pending_tags", None)
    pending_paths: set[str] | None = getattr(_local, "pending_paths", None)
    first_in_tx = pending_tags is None

    if pending_tags is None:
        pending_tags = set()
        _local.pending_tags = pending_tags
    if pending_paths is None:
        pending_paths = set()
        _local.pending_paths = pending_paths

    pending_tags.update(tags)
    if paths:
        pending_paths.update(paths)

    if not first_in_tx:
        return

    def _flush() -> None:
        tags_to_send = list(getattr(_local, "pending_tags", set()))
        paths_to_send = list(getattr(_local, "pending_paths", set()))
        _local.pending_tags = None
        _local.pending_paths = None

        frontend_url = getattr(settings, "FRONTEND_URL", "").rstrip("/")
        if not frontend_url or not tags_to_send:
            return

        secret = getattr(settings, "REVALIDATE_SECRET", "titan-revalidate-default-secret")
        payload = json.dumps({"tags": tags_to_send, "paths": paths_to_send}).encode("utf-8")
        req = urllib.request.Request(
            f"{frontend_url}/api/revalidate",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-revalidate-secret": secret,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2.0):
                pass
        except Exception as exc:
            logger.debug("Frontend cache revalidation skipped or failed: %s", exc)

    transaction.on_commit(_flush)


@receiver([post_save, post_delete], sender=ProductVariant)
def keep_product_price_in_sync(sender, instance: ProductVariant, **kwargs) -> None:
    """The product's price/stock cache must follow its options however they change (admin, API, stock)."""
    sync_variant_summary(instance.product_id)
    _schedule_frontend_revalidation(["products", "home"], ["/store"])


@receiver([post_save, post_delete], sender=Product)
def revalidate_on_product_change(sender, instance: Product, **kwargs) -> None:
    tags = ["products", "games", "home", f"product-{instance.slug}"]
    paths = ["/store", f"/product/{instance.slug}"]
    if instance.game_id and instance.game:
        tags.append(f"products-{instance.game.slug}")
        tags.append(f"game-{instance.game.slug}")
        paths.append(f"/store/{instance.game.slug}")
    _schedule_frontend_revalidation(tags, paths)


@receiver([post_save, post_delete], sender=Game)
def revalidate_on_game_change(sender, instance: Game, **kwargs) -> None:
    _schedule_frontend_revalidation(
        ["games", "products", "home", f"game-{instance.slug}", f"products-{instance.slug}"],
        ["/store", f"/store/{instance.slug}"],
    )


@receiver([post_save, post_delete], sender=ProductCategory)
def revalidate_on_category_change(sender, instance: ProductCategory, **kwargs) -> None:
    _schedule_frontend_revalidation(["products", "games", "home"], ["/store"])


@receiver([post_save, post_delete], sender=Promo)
def revalidate_on_promo_change(sender, instance: Promo, **kwargs) -> None:
    _schedule_frontend_revalidation(["promos", "home"], ["/store", "/"])
