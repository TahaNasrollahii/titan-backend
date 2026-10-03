from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import ProductVariant
from .services import sync_variant_summary


@receiver([post_save, post_delete], sender=ProductVariant)
def keep_product_price_in_sync(sender, instance: ProductVariant, **kwargs) -> None:
    """The product's price/stock cache must follow its options however they change (admin, API, stock)."""
    sync_variant_summary(instance.product_id)
