from rest_framework import serializers

from apps.catalog.models import Product
from apps.catalog.serializers import GameMiniSerializer
from apps.core.serializers import ImageUrlField

from .models import Announcement, ContactChannel, Guide, GuideFaq, Promo


class PromoSerializer(serializers.ModelSerializer):
    image = ImageUrlField()
    background_image = ImageUrlField()
    product = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    tournament = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Promo
        fields = [
            "id",
            "placement",
            "title",
            "subtitle",
            "badge",
            "image",
            "background_image",
            "background_gradient",
            "price",
            "original_price",
            "discount_label",
            "product",
            "tournament",
            "link",
            "layout",
            "starts_at",
            "ends_at",
        ]


class AnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Announcement
        fields = ["id", "title", "text", "icon", "link"]


class ContactChannelSerializer(serializers.ModelSerializer):
    icon = ImageUrlField()

    class Meta:
        model = ContactChannel
        fields = ["kind", "title", "description", "url", "icon", "action_label", "is_primary", "is_online"]


class ContactInfoSerializer(serializers.Serializer):
    support_online = serializers.BooleanField()
    channels = ContactChannelSerializer(many=True)


class GuideSummarySerializer(serializers.ModelSerializer):
    game = GameMiniSerializer(read_only=True)

    class Meta:
        model = Guide
        fields = ["slug", "title", "category", "game", "summary", "published_at", "updated_at"]


class GuideFaqSerializer(serializers.ModelSerializer):
    class Meta:
        model = GuideFaq
        fields = ["question", "answer"]


class GuideProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["slug", "title"]


class GuideDetailSerializer(GuideSummarySerializer):
    faqs = GuideFaqSerializer(many=True, read_only=True)
    products = GuideProductSerializer(many=True, read_only=True)
    related = serializers.SerializerMethodField()
    og_image = ImageUrlField()

    class Meta(GuideSummarySerializer.Meta):
        fields = [
            *GuideSummarySerializer.Meta.fields,
            "body",
            "faqs",
            "products",
            "related",
            "meta_title",
            "meta_description",
            "noindex",
            "og_image",
            "canonical_url",
        ]

    def get_related(self, guide: Guide) -> list[dict]:
        """Up to four other guides: same game first, then the same category."""
        others = Guide.objects.published().exclude(pk=guide.pk).select_related("game")
        related = list(others.filter(game_id=guide.game_id)[:4]) if guide.game_id else []
        if len(related) < 4:
            seen = [g.pk for g in related]
            related += list(others.filter(category=guide.category).exclude(pk__in=seen)[: 4 - len(related)])
        return GuideSummarySerializer(related, many=True, context=self.context).data
