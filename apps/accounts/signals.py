from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import RankTier
from .ranks import clear_tier_cache


@receiver([post_save, post_delete], sender=RankTier)
def invalidate_rank_tier_cache(**kwargs) -> None:
    clear_tier_cache()
