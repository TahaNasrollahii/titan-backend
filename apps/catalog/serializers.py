from rest_framework import serializers

from apps.accounts.serializers import UserMiniSerializer
from apps.core.serializers import ImageUrlField

from .models import (
    Game,
    Platform,
    Product,
    ProductCategory,
    ProductImage,
    ProductVariant,
    Review,
    WishlistItem,
)


class GameMiniSerializer(serializers.ModelSerializer):
    icon_image = ImageUrlField()

    class Meta:
        model = Game
        fields = ["slug", "title", "title_en", "icon_image", "accent_color", "credential_type"]


class GameSerializer(serializers.ModelSerializer):
    cover_image = ImageUrlField()
    logo_image = ImageUrlField()
    background_image = ImageUrlField()
    character_image = ImageUrlField()
    og_image = ImageUrlField()
    icon_image = ImageUrlField()
    product_count = serializers.IntegerField(read_only=True)
    tournament_count = serializers.IntegerField(read_only=True)
    player_count = serializers.IntegerField(read_only=True)

    def to_representation(self, instance: Game):
        data = super().to_representation(instance)
        if not data.get("meta_title"):
            data["meta_title"] = f"خرید آیتم و محصولات {instance.title} ({instance.title_en})"[:70]
        if not data.get("meta_description"):
            fallback_desc = (
                instance.intro_text
                or instance.description
                or f"خرید آنلاین محصولات، گیفت کارت و آیتم‌های {instance.title} ({instance.title_en}) با تحویل آنی و گارانتی معتبر در فروشگاه تایتان."
            )
            data["meta_description"] = fallback_desc[:160]
        if not data.get("image_alt"):
            data["image_alt"] = f"{instance.title} - {instance.title_en}"[:150]
        if not data.get("og_image"):
            data["og_image"] = data.get("cover_image") or data.get("background_image")
        return data

    class Meta:
        model = Game
        fields = [
            "slug",
            "title",
            "title_en",
            "description",
            "genre",
            "kind",
            "credential_type",
            "cover_image",
            "logo_image",
            "background_image",
            "character_image",
            "icon_image",
            "accent_color",
            "accent_gradient",
            "is_featured",
            "product_count",
            "tournament_count",
            "player_count",
            "intro_text",
            "bottom_seo_text",
            "meta_title",
            "meta_description",
            "noindex",
            "og_image",
            "canonical_url",
            "image_alt",
            "updated_at",
        ]


class PlatformSerializer(serializers.ModelSerializer):
    icon = ImageUrlField()

    class Meta:
        model = Platform
        fields = ["slug", "name", "icon"]


class ProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = [
            "slug",
            "name",
            "description",
            "intro_text",
            "bottom_seo_text",
            "meta_title",
            "meta_description",
            "noindex",
            "canonical_url",
            "image_alt",
            "updated_at",
        ]

    def to_representation(self, instance: ProductCategory):
        data = super().to_representation(instance)
        if not data.get("meta_title"):
            data["meta_title"] = f"خرید {instance.name} بازی"[:70]
        if not data.get("meta_description"):
            fallback_desc = (
                instance.intro_text
                or instance.description
                or f"خرید آنلاین انواع {instance.name} اورجینال با تحویل آنی و بهترین قیمت در تایتان."
            )
            data["meta_description"] = fallback_desc[:160]
        if not data.get("image_alt"):
            data["image_alt"] = instance.name
        return data


class ProductVariantSerializer(serializers.ModelSerializer):
    discount_percent = serializers.IntegerField(read_only=True)
    in_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProductVariant
        fields = ["id", "label", "price", "original_price", "discount_percent", "in_stock"]


class ProductImageSerializer(serializers.ModelSerializer):
    image = ImageUrlField()

    class Meta:
        model = ProductImage
        fields = ["image", "alt"]

    def to_representation(self, instance: ProductImage):
        data = super().to_representation(instance)
        if not data.get("alt") and instance.product_id:
            data["alt"] = instance.product.title
        return data


class ProductListSerializer(serializers.ModelSerializer):
    """``price`` is the fixed price, or the cheapest option when ``hasVariants`` (then ``priceMax`` is the
    most expensive option, so cards can show "from X" / "X - Y")."""

    image = ImageUrlField()
    game = GameMiniSerializer(read_only=True)
    category = ProductCategorySerializer(read_only=True)
    platforms = serializers.SlugRelatedField(slug_field="slug", many=True, read_only=True)
    discount_percent = serializers.IntegerField(read_only=True)
    in_stock = serializers.BooleanField(read_only=True)
    badges = serializers.ListField(child=serializers.CharField(), read_only=True)
    rating = serializers.FloatField(source="rating_avg", read_only=True)
    is_wishlisted = serializers.BooleanField(read_only=True, default=False)
    credential_type = serializers.CharField(source="effective_credential_type", read_only=True)

    def to_representation(self, instance: Product):
        data = super().to_representation(instance)
        if not data.get("sku"):
            data["sku"] = f"TITAN-{instance.id}"
        if not data.get("image_alt"):
            data["image_alt"] = instance.title
        return data

    class Meta:
        model = Product
        fields = [
            "id",
            "slug",
            "title",
            "subtitle",
            "image",
            "image_alt",
            "sku",
            "price",
            "original_price",
            "discount_percent",
            "has_variants",
            "price_max",
            "rating",
            "review_count",
            "badges",
            "in_stock",
            "vendor",
            "delivery_type",
            "credential_type",
            "game",
            "category",
            "platforms",
            "is_wishlisted",
            "created_at",
            "updated_at",
        ]


class ProductDetailSerializer(ProductListSerializer):
    platforms = PlatformSerializer(many=True, read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    gallery = ProductImageSerializer(many=True, read_only=True)
    requires_game_account = serializers.BooleanField(read_only=True)

    def to_representation(self, instance: Product):
        data = super().to_representation(instance)
        if not data.get("meta_title"):
            base_title = (
                f"خرید {instance.title} ({instance.game.title})"
                if instance.game_id and instance.game
                else f"خرید {instance.title}"
            )
            data["meta_title"] = base_title[:70]
        if not data.get("meta_description"):
            fallback_desc = (
                instance.description
                or instance.subtitle
                or f"خرید آنلاین {instance.title} با تحویل آنی، قیمت مناسب و گارانتی معتبر در فروشگاه تایتان."
            )
            data["meta_description"] = fallback_desc[:160]
        if not data.get("og_image"):
            data["og_image"] = data.get("image")
        return data
    og_image = ImageUrlField()

    class Meta(ProductListSerializer.Meta):
        fields = [
            *ProductListSerializer.Meta.fields,
            "description",
            "delivery_info",
            "requires_game_account",
            "stock",
            "features",
            "tags",
            "specs",
            "variants",
            "gallery",
            "gtin",
            "mpn",
            "meta_title",
            "meta_description",
            "noindex",
            "og_image",
            "canonical_url",
        ]


class ReviewSerializer(serializers.ModelSerializer):
    """``authorEmail`` is accepted on submit but never returned, so reviewers' emails stay private."""

    user = UserMiniSerializer(read_only=True)
    author_name = serializers.CharField(max_length=80, trim_whitespace=True)
    author_email = serializers.EmailField(write_only=True)

    class Meta:
        model = Review
        fields = [
            "id",
            "user",
            "author_name",
            "author_email",
            "rating",
            "comment",
            "verified_purchase",
            "created_at",
        ]
        read_only_fields = ["id", "user", "verified_purchase", "created_at"]
        extra_kwargs = {"rating": {"min_value": 1, "max_value": 5}}


class WishlistItemSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)

    class Meta:
        model = WishlistItem
        fields = ["product", "created_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["product"]["is_wishlisted"] = True
        return data


class WishlistAddSerializer(serializers.Serializer):
    product = serializers.SlugRelatedField(slug_field="slug", queryset=Product.objects.active())
