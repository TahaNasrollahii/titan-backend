from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import percent_off
from apps.core.validators import IMAGE_VALIDATORS


class Game(TimeStampedModel):
    class Kind(models.TextChoices):
        GAME = "game", _("Game")
        SERVICE = "service", _("Service")  # e.g. premium accounts (Spotify, Telegram…)

    slug = models.SlugField(unique=True)
    title = models.CharField(_("title"), max_length=80)
    title_en = models.CharField(_("English title"), max_length=80)
    description = models.TextField(blank=True)
    genre = models.CharField(max_length=60, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.GAME)

    cover_image = models.ImageField(upload_to="games/", blank=True, validators=IMAGE_VALIDATORS)
    logo_image = models.ImageField(upload_to="games/", blank=True, validators=IMAGE_VALIDATORS)
    background_image = models.ImageField(upload_to="games/", blank=True, validators=IMAGE_VALIDATORS)
    character_image = models.ImageField(upload_to="games/", blank=True, validators=IMAGE_VALIDATORS)
    icon_image = models.ImageField(upload_to="games/", blank=True, validators=IMAGE_VALIDATORS)

    accent_color = models.CharField(max_length=16, blank=True)
    accent_gradient = models.CharField(max_length=120, blank=True)
    is_featured = models.BooleanField(default=False, help_text=_("Shown as a store category tab."))
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "title_en"]

    def __str__(self) -> str:
        return self.title_en


class Platform(models.Model):
    """A device family a product works on (PC, PlayStation, Xbox, Nintendo, mobile)."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=40)
    icon = models.ImageField(upload_to="platforms/", blank=True, validators=IMAGE_VALIDATORS)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class ProductCategory(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=60)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = _("product categories")

    def __str__(self) -> str:
        return self.name


class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class Product(TimeStampedModel):
    """A sellable item. Two shapes are supported:

    * **Fixed price** (e.g. "Fortnite Crew Pack"): ``price``/``original_price``/``stock`` are set directly.
    * **With options** (e.g. V-Bucks 1,000 / 2,800 / 5,000): each ``ProductVariant`` has its own price and
      stock. The product's ``price`` (cheapest option), ``original_price``, ``price_max`` and ``stock``
      (total) then become a cache kept in sync by ``services.sync_variant_summary``.
    """

    class DeliveryType(models.TextChoices):
        CODE = "code", _("Redeem code")
        ACCOUNT = "account", _("Direct account top-up")
        MANUAL = "manual", _("Manual fulfilment")

    slug = models.SlugField(unique=True, max_length=120)
    title = models.CharField(_("title"), max_length=150)
    subtitle = models.CharField(max_length=150, blank=True)
    game = models.ForeignKey(Game, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, related_name="products")
    vendor = models.CharField(_("vendor / store"), max_length=60, blank=True)
    platforms = models.ManyToManyField(Platform, blank=True, related_name="products")

    description = models.TextField(blank=True)
    delivery_info = models.CharField(max_length=200, blank=True)
    delivery_type = models.CharField(max_length=10, choices=DeliveryType.choices, default=DeliveryType.CODE)
    image = models.ImageField(upload_to="products/", blank=True, validators=IMAGE_VALIDATORS)

    price = models.PositiveBigIntegerField(
        _("price (toman)"),
        null=True,
        blank=True,
        help_text=_("Required for fixed-price products. With options it is the cheapest option (automatic)."),
    )
    original_price = models.PositiveBigIntegerField(_("price before discount"), null=True, blank=True)
    price_max = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        editable=False,
        help_text=_("Most expensive option; empty for fixed-price products."),
    )
    has_variants = models.BooleanField(default=False, editable=False, db_index=True)
    stock = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Empty means unlimited. With options it is their total stock (automatic)."),
    )
    is_active = models.BooleanField(default=True)
    is_bestseller = models.BooleanField(default=False)
    is_new = models.BooleanField(default=False)
    popularity = models.PositiveIntegerField(default=0, db_index=True)

    features = models.JSONField(default=list, blank=True, help_text=_('[{"title", "description", "icon"}]'))
    tags = models.JSONField(default=list, blank=True)
    specs = models.JSONField(default=dict, blank=True)

    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    review_count = models.PositiveIntegerField(default=0)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["-popularity", "-created_at"]
        indexes = [models.Index(fields=["price"]), models.Index(fields=["is_active", "-popularity"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(original_price__isnull=True) | Q(original_price__gte=F("price")),
                name="product_original_price_gte_price",
            )
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def discount_percent(self) -> int:
        return percent_off(self.price, self.original_price) if self.price is not None else 0

    @property
    def is_purchasable(self) -> bool:
        """A fixed-price product needs a price; a product with options is priced by them."""
        return self.is_active and (self.has_variants or self.price is not None)

    @property
    def in_stock(self) -> bool:
        return self.stock is None or self.stock > 0

    @property
    def badges(self) -> list[str]:
        badges = []
        if self.is_bestseller:
            badges.append("bestseller")
        if self.discount_percent:
            badges.append("discount")
        if self.is_new:
            badges.append("new")
        return badges

    @property
    def requires_game_account(self) -> bool:
        return self.delivery_type == self.DeliveryType.ACCOUNT


class ProductVariant(models.Model):
    """One purchasable option of a product, e.g. "2,800 V-Bucks", with its own price and stock."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    label = models.CharField(max_length=80)
    price = models.PositiveBigIntegerField(_("price (toman)"))
    original_price = models.PositiveBigIntegerField(_("price before discount"), null=True, blank=True)
    stock = models.PositiveIntegerField(null=True, blank=True, help_text=_("Empty means unlimited."))
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "price"]
        constraints = [
            models.UniqueConstraint(fields=["product", "label"], name="unique_variant_label"),
            models.CheckConstraint(
                condition=Q(original_price__isnull=True) | Q(original_price__gte=F("price")),
                name="variant_original_price_gte_price",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product} — {self.label}"

    @property
    def discount_percent(self) -> int:
        return percent_off(self.price, self.original_price)

    @property
    def in_stock(self) -> bool:
        return self.stock is None or self.stock > 0


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="gallery")
    image = models.ImageField(upload_to="products/gallery/", validators=IMAGE_VALIDATORS)
    alt = models.CharField(max_length=150, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.alt or f"Image #{self.pk}"


class Review(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    author_name = models.CharField(_("name"), max_length=80, help_text=_("Shown publicly with the review."))
    author_email = models.EmailField(_("email"), help_text=_("Private; never shown publicly."))
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True, max_length=2000)
    verified_purchase = models.BooleanField(default=False)
    is_approved = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["product", "user"], name="unique_review_per_user"),
            models.CheckConstraint(condition=Q(rating__gte=1, rating__lte=5), name="review_rating_range"),
        ]

    def __str__(self) -> str:
        return f"{self.product} — {self.rating}★"


class WishlistItem(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wishlist")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="wishlisted_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "product"], name="unique_wishlist_item")]

    def __str__(self) -> str:
        return f"{self.user} ♥ {self.product}"
