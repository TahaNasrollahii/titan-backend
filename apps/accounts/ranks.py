"""Rank tier lookup. Tiers are a tiny, rarely-changing table, so they are cached in-process."""

from django.core.cache import cache

from .models import RankTier

_CACHE_KEY = "accounts:rank_tiers"


def all_tiers() -> list[RankTier]:
    tiers = cache.get(_CACHE_KEY)
    if tiers is None:
        tiers = list(RankTier.objects.order_by("min_points"))
        cache.set(_CACHE_KEY, tiers, timeout=60 * 60)
    return tiers


def tier_for_points(points: int) -> RankTier | None:
    """Return the highest tier whose threshold is <= ``points``."""
    current = None
    for tier in all_tiers():
        if points >= tier.min_points:
            current = tier
        else:
            break
    return current


def clear_tier_cache() -> None:
    cache.delete(_CACHE_KEY)
