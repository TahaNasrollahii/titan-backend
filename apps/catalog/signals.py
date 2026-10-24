import json
import logging
import threading
import urllib.request

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from apps.content.models import Promo
from apps.core.models import Redirect
from apps.tournaments.models import Tournament

from .models import Game, Product, ProductCategory, ProductVariant
from .services import sync_variant_summary

SEO_SITEMAP_CACHE_KEY = "seo-sitemap-v1"
SEO_REDIRECTS_CACHE_KEY = "seo-redirects-v1"


def _invalidate_seo_caches(*, include_redirects: bool = False) -> None:
    keys = [SEO_SITEMAP_CACHE_KEY]
    if include_redirects:
        keys.append(SEO_REDIRECTS_CACHE_KEY)
    cache.delete_many(keys)

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

        secret = getattr(settings, "REVALIDATE_SECRET", "")
        if not secret:
            logger.warning("REVALIDATE_SECRET is not set; skipping frontend cache revalidation.")
            return
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


@receiver(pre_save, sender=Product)
def _remember_previous_product_slug(sender, instance: Product, **kwargs) -> None:
    if not instance.pk:
        return
    prev = Product.objects.filter(pk=instance.pk).values_list("slug", flat=True).first()
    if prev and prev != instance.slug:
        instance._previous_slug = prev


@receiver(pre_save, sender=Game)
def _remember_previous_game_slug(sender, instance: Game, **kwargs) -> None:
    if not instance.pk:
        return
    prev = Game.objects.filter(pk=instance.pk).values_list("slug", flat=True).first()
    if prev and prev != instance.slug:
        instance._previous_slug = prev


@receiver(pre_save, sender=Tournament)
def _remember_previous_tournament_slug(sender, instance: Tournament, **kwargs) -> None:
    if not instance.pk:
        return
    prev = Tournament.objects.filter(pk=instance.pk).values_list("slug", flat=True).first()
    if prev and prev != instance.slug:
        instance._previous_slug = prev


@receiver([post_save, post_delete], sender=Product)
def revalidate_on_product_change(sender, instance: Product, **kwargs) -> None:
    prev_slug = getattr(instance, "_previous_slug", None)
    slug_changed = bool(prev_slug and prev_slug != instance.slug)
    if slug_changed:
        Redirect.record_slug_change("product", prev_slug, instance.slug)
    _invalidate_seo_caches(include_redirects=slug_changed)

    tags = ["products", "games", "home", "sitemap", f"product-{instance.slug}"]
    paths = ["/store", "/sitemap.xml", f"/product/{instance.slug}"]
    if slug_changed:
        tags.extend(["redirects", f"product-{prev_slug}"])
        paths.append(f"/product/{prev_slug}")
    if instance.game_id and instance.game:
        tags.append(f"products-{instance.game.slug}")
        tags.append(f"game-{instance.game.slug}")
        paths.append(f"/store/{instance.game.slug}")
    _schedule_frontend_revalidation(tags, paths)


@receiver([post_save, post_delete], sender=Game)
def revalidate_on_game_change(sender, instance: Game, **kwargs) -> None:
    prev_slug = getattr(instance, "_previous_slug", None)
    slug_changed = bool(prev_slug and prev_slug != instance.slug)
    if slug_changed:
        Redirect.record_slug_change("store", prev_slug, instance.slug)
    _invalidate_seo_caches(include_redirects=slug_changed)

    tags = ["games", "products", "home", "sitemap", f"game-{instance.slug}", f"products-{instance.slug}"]
    paths = ["/store", "/sitemap.xml", f"/store/{instance.slug}"]
    if slug_changed:
        tags.extend(["redirects", f"game-{prev_slug}", f"products-{prev_slug}"])
        paths.append(f"/store/{prev_slug}")
    _schedule_frontend_revalidation(tags, paths)


@receiver([post_save, post_delete], sender=ProductCategory)
def revalidate_on_category_change(sender, instance: ProductCategory, **kwargs) -> None:
    _invalidate_seo_caches()
    _schedule_frontend_revalidation(["products", "games", "home", "sitemap"], ["/store", "/sitemap.xml"])


@receiver([post_save, post_delete], sender=Tournament)
def revalidate_on_tournament_change(sender, instance: Tournament, **kwargs) -> None:
    prev_slug = getattr(instance, "_previous_slug", None)
    slug_changed = bool(prev_slug and prev_slug != instance.slug)
    if slug_changed:
        Redirect.record_slug_change("tournaments", prev_slug, instance.slug)
    _invalidate_seo_caches(include_redirects=slug_changed)

    tags = ["tournaments", "home", "sitemap", f"tournament-{instance.slug}"]
    paths = ["/tournaments", "/tournament", "/sitemap.xml", f"/tournaments/{instance.slug}"]
    if slug_changed:
        tags.extend(["redirects", f"tournament-{prev_slug}"])
        paths.append(f"/tournaments/{prev_slug}")
    _schedule_frontend_revalidation(tags, paths)


@receiver([post_save, post_delete], sender=Redirect)
def revalidate_on_redirect_change(sender, instance: Redirect, **kwargs) -> None:
    _invalidate_seo_caches(include_redirects=True)
    _schedule_frontend_revalidation(["redirects"], [instance.old_path, instance.new_path])


@receiver([post_save, post_delete], sender=Promo)
def revalidate_on_promo_change(sender, instance: Promo, **kwargs) -> None:
    _schedule_frontend_revalidation(["promos", "home"], ["/store", "/"])
