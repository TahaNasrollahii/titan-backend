from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import SeoMetadataMixin, TimeStampedModel
from apps.core.validators import IMAGE_VALIDATORS


class ActiveWindowQuerySet(models.QuerySet):
    def live(self):
        """Enabled items whose optional start/end window contains *now*."""
        now = timezone.now()
        return self.filter(
            Q(is_active=True),
            Q(starts_at__isnull=True) | Q(starts_at__lte=now),
            Q(ends_at__isnull=True) | Q(ends_at__gt=now),
        )


class Promo(TimeStampedModel):
    """A merchandising banner/slide (store promos, home hero, tournament hero)."""

    class Placement(models.TextChoices):
        STORE_DISCOUNT = "store_discount", _("Store — special offers")
        STORE_BESTSELLER = "store_bestseller", _("Store — best sellers")
        HOME_HERO = "home_hero", _("Home — hero")
        TOURNAMENT_HERO = "tournament_hero", _("Tournaments — hero")

    placement = models.CharField(max_length=20, choices=Placement.choices, db_index=True)
    title = models.CharField(max_length=120)
    subtitle = models.CharField(max_length=200, blank=True)
    badge = models.CharField(max_length=40, blank=True)
    image = models.ImageField(upload_to="promos/", blank=True, validators=IMAGE_VALIDATORS)
    background_image = models.ImageField(upload_to="promos/", blank=True, validators=IMAGE_VALIDATORS)
    background_gradient = models.CharField(max_length=200, blank=True)
    price = models.PositiveBigIntegerField(null=True, blank=True)
    original_price = models.PositiveBigIntegerField(null=True, blank=True)
    discount_label = models.CharField(max_length=20, blank=True)
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="promos"
    )
    tournament = models.ForeignKey(
        "tournaments.Tournament", null=True, blank=True, on_delete=models.SET_NULL, related_name="promos"
    )
    link = models.CharField(max_length=300, blank=True, help_text=_("Internal path or absolute URL."))
    layout = models.JSONField(
        default=dict, blank=True, help_text=_('Art placement, e.g. {"scale": 1.1, "x": 0}')
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True, help_text=_("Drives the countdown on the banner."))
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    objects = ActiveWindowQuerySet.as_manager()

    class Meta:
        ordering = ["placement", "sort_order", "-created_at"]
        verbose_name = _("Promo")
        verbose_name_plural = _("Promos")

    def __str__(self) -> str:
        return f"[{self.placement}] {self.title}"


class Announcement(TimeStampedModel):
    """Short live notices shown as toasts on the home page."""

    title = models.CharField(max_length=120)
    text = models.CharField(max_length=250)
    icon = models.CharField(max_length=40, blank=True)
    link = models.CharField(max_length=300, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    objects = ActiveWindowQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "-created_at"]
        verbose_name = _("Announcement")
        verbose_name_plural = _("Announcements")

    def __str__(self) -> str:
        return self.title


class ContactChannel(models.Model):
    class Kind(models.TextChoices):
        DISCORD = "discord", "Discord"
        TELEGRAM_CHANNEL = "telegram_channel", _("Telegram channel")
        TELEGRAM_SUPPORT = "telegram_support", _("Telegram support")
        LIVE_CHAT = "live_chat", _("Live chat")
        EMAIL = "email", _("Email")
        PHONE = "phone", _("Phone")

    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    url = models.CharField(max_length=300, help_text=_("https://…, mailto:… or tel:…"))
    icon = models.ImageField(upload_to="contact/", blank=True, validators=IMAGE_VALIDATORS)
    action_label = models.CharField(max_length=30, blank=True)
    is_primary = models.BooleanField(default=False)
    is_online = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = _("Contact channel")
        verbose_name_plural = _("Contact channels")

    def __str__(self) -> str:
        return self.title


class SiteSetting(models.Model):
    """Simple key/value switches editable from the admin (e.g. ``support_online``)."""

    key = models.SlugField(max_length=60, unique=True)
    value = models.JSONField(default=dict, blank=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["key"]
        verbose_name = _("Site setting")
        verbose_name_plural = _("Site settings")

    def __str__(self) -> str:
        return self.key

    @classmethod
    def get(cls, key: str, default=None):
        setting = cls.objects.filter(key=key).first()
        return setting.value if setting is not None else default


class GuideQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True)


class Guide(TimeStampedModel, SeoMetadataMixin):
    """A how-to article in the /guides hub: buying and activating products, account setup, site help."""

    class Category(models.TextChoices):
        PURCHASE = "purchase", _("Buying & activation")
        ACCOUNT = "account", _("Account setup")
        SITE = "site", _("Using Titan")
        TOURNAMENT = "tournament", _("Tournaments")

    slug = models.SlugField(unique=True, max_length=120)
    title = models.CharField(_("title"), max_length=150)
    category = models.CharField(max_length=12, choices=Category.choices, db_index=True)
    game = models.ForeignKey(
        "catalog.Game", null=True, blank=True, on_delete=models.SET_NULL, related_name="guides"
    )
    products = models.ManyToManyField(
        "catalog.Product",
        blank=True,
        related_name="guides",
        help_text=_("Product pages that link to this guide."),
    )
    summary = models.CharField(
        max_length=300, help_text=_("One or two sentences shown on the guide card and under the title.")
    )
    body = models.TextField(
        help_text=_(
            "Lines starting with ## or ### are headings, - is a bullet, 1. is a numbered step, "
            "**text** is bold and [text](/path) is a link. Leave a blank line between paragraphs."
        )
    )
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    objects = GuideQuerySet.as_manager()

    class Meta:
        ordering = ["category", "sort_order", "title"]
        verbose_name = _("Guide")
        verbose_name_plural = _("Guides")

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if self.is_published and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)


class GuideFaq(models.Model):
    guide = models.ForeignKey(Guide, on_delete=models.CASCADE, related_name="faqs")
    question = models.CharField(max_length=200)
    answer = models.TextField()
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = _("Guide question")
        verbose_name_plural = _("Guide questions")

    def __str__(self) -> str:
        return self.question
