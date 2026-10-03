from rest_framework import serializers

from apps.core.serializers import ImageUrlField

from .models import Announcement, ContactChannel, Promo


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
