from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from .validators import IMAGE_VALIDATORS


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SeoMetadataMixin(models.Model):
    """Reusable SEO metadata fields for indexable storefront entities."""

    meta_title = models.CharField(
        _("meta title"),
        max_length=70,
        blank=True,
        help_text=_("Custom SEO title (~50–60 chars). Leave blank for automatic fallback."),
    )
    meta_description = models.CharField(
        _("meta description"),
        max_length=160,
        blank=True,
        help_text=_("Custom SEO description (~140–160 chars). Leave blank for automatic fallback."),
    )
    noindex = models.BooleanField(
        _("noindex"),
        default=False,
        db_index=True,
        help_text=_("Prevent search engines from indexing this page."),
    )
    og_image = models.ImageField(
        _("Open Graph image"),
        upload_to="seo/og/",
        blank=True,
        validators=IMAGE_VALIDATORS,
        help_text=_("Recommended 1200×630px social preview image. Falls back to primary image when empty."),
    )
    canonical_url = models.URLField(
        _("canonical URL"),
        blank=True,
        help_text=_("Optional canonical URL override. Leave blank for self-referencing canonical."),
    )
    image_alt = models.CharField(
        _("main image alt text"),
        max_length=150,
        blank=True,
        help_text=_("Descriptive alt text for the main image. Falls back to title when empty."),
    )

    class Meta:
        abstract = True


def normalize_redirect_path(path: str) -> str:
    cleaned = (path or "").strip()
    if not cleaned:
        return ""
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    if len(cleaned) > 1:
        cleaned = cleaned.rstrip("/")
    return cleaned


class Redirect(TimeStampedModel):
    """301/302 redirect rule for changed slugs or retired URLs, with automatic chain flattening."""

    old_path = models.CharField(
        _("old path"),
        max_length=255,
        unique=True,
        db_index=True,
        help_text=_("Source path starting with /, e.g. /product/old-slug"),
    )
    new_path = models.CharField(
        _("new path"),
        max_length=255,
        help_text=_("Target path starting with /, e.g. /product/new-slug"),
    )
    is_permanent = models.BooleanField(_("permanent (301)"), default=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = _("Redirect")
        verbose_name_plural = _("Redirects")

    def __str__(self) -> str:
        code = 301 if self.is_permanent else 302
        return f"{self.old_path} → {self.new_path} ({code})"

    def clean(self) -> None:
        super().clean()
        self.old_path = normalize_redirect_path(self.old_path)
        self.new_path = normalize_redirect_path(self.new_path)
        if not self.old_path or not self.new_path:
            raise ValidationError(_("Both old_path and new_path are required."))
        if self.old_path == self.new_path:
            raise ValidationError(_("A redirect cannot point to the same path."))

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        with transaction.atomic():
            # If new_path currently redirects somewhere else via a manual rule, follow it unless it loops back
            hop = (
                Redirect.objects.filter(old_path=self.new_path, is_active=True)
                .exclude(pk=self.pk)
                .first()
            )
            if hop and hop.new_path != self.old_path:
                self.new_path = hop.new_path

            super().save(*args, **kwargs)

            # Flatten any incoming redirects pointing to old_path so they jump straight to new_path
            Redirect.objects.filter(new_path=self.old_path).exclude(pk=self.pk).exclude(
                old_path=self.new_path
            ).update(new_path=self.new_path)
            # Remove any loop entries where an incoming redirect had old_path == self.new_path
            Redirect.objects.filter(old_path=self.new_path, new_path=self.old_path).exclude(
                pk=self.pk
            ).delete()

    @classmethod
    def record_slug_change(cls, prefix: str, old_slug: str, new_slug: str) -> "Redirect | None":
        """Record a 301 redirect when an entity's slug changes, flattening chains and preventing loops."""
        if not old_slug or not new_slug or old_slug == new_slug:
            return None
        old_path = normalize_redirect_path(f"/{prefix.strip('/')}/{old_slug}")
        new_path = normalize_redirect_path(f"/{prefix.strip('/')}/{new_slug}")
        if old_path == new_path:
            return None
        with transaction.atomic():
            # new_path is now a live canonical destination; remove any old redirect originating from new_path
            cls.objects.filter(old_path=new_path).delete()
            redirect_obj, _ = cls.objects.update_or_create(
                old_path=old_path,
                defaults={"new_path": new_path, "is_permanent": True, "is_active": True},
            )
            return redirect_obj

