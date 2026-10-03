from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from apps.accounts.models import GameAccount
from apps.catalog.models import Product, ProductVariant
from apps.core.serializers import ImageUrlField
from apps.payments.models import PaymentMethod

from .models import MAX_QUANTITY_PER_LINE, CartItem, Order, OrderItem


class CartProductSerializer(serializers.ModelSerializer):
    image = ImageUrlField()
    requires_game_account = serializers.BooleanField(read_only=True)
    in_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = ["id", "slug", "title", "image", "delivery_type", "requires_game_account", "in_stock"]


class CartVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = ["id", "label"]


class CartItemSerializer(serializers.ModelSerializer):
    product = CartProductSerializer(read_only=True)
    variant = CartVariantSerializer(read_only=True)
    unit_price = serializers.IntegerField(read_only=True)
    unit_original_price = serializers.IntegerField(read_only=True)
    line_total = serializers.IntegerField(read_only=True)

    class Meta:
        model = CartItem
        fields = ["id", "product", "variant", "quantity", "unit_price", "unit_original_price", "line_total"]


class CartSerializer(serializers.Serializer):
    """Serializes ``orders.services.CartSummary``."""

    items = CartItemSerializer(many=True)
    count = serializers.IntegerField()
    subtotal = serializers.IntegerField()
    discount = serializers.IntegerField()
    total = serializers.IntegerField()
    requires_game_account = serializers.BooleanField()


class CartLineInputSerializer(serializers.Serializer):
    product = serializers.SlugRelatedField(slug_field="slug", queryset=Product.objects.all())
    variant = serializers.PrimaryKeyRelatedField(
        queryset=ProductVariant.objects.all(), required=False, allow_null=True
    )
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY_PER_LINE, default=1)


class CartItemUpdateSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=0, max_value=MAX_QUANTITY_PER_LINE)


class CartMergeSerializer(serializers.Serializer):
    items = CartLineInputSerializer(many=True, allow_empty=True)


class SkippedLineSerializer(serializers.Serializer):
    product = serializers.CharField()
    reason = serializers.CharField()


class CartMergeResponseSerializer(serializers.Serializer):
    cart = CartSerializer()
    skipped = SkippedLineSerializer(many=True)


class OrderItemSerializer(serializers.ModelSerializer):
    image = ImageUrlField()
    product = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    delivered_code = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product",
            "title",
            "variant_label",
            "image",
            "delivery_type",
            "unit_price",
            "unit_original_price",
            "quantity",
            "line_total",
            "delivered_code",
            "delivered_at",
        ]

    def get_delivered_code(self, item: OrderItem) -> str | None:
        return item.delivered_code if item.delivered_at else None


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "number",
            "status",
            "payment_method",
            "subtotal",
            "discount",
            "total",
            "game_account_title",
            "game_account_username",
            "items",
            "paid_at",
            "completed_at",
            "created_at",
        ]


class CheckoutSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices)
    game_account = serializers.PrimaryKeyRelatedField(
        queryset=GameAccount.objects.none(), required=False, allow_null=True
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            self.fields["game_account"].queryset = GameAccount.objects.filter(user=request.user)
        self.fields["game_account"].error_messages["does_not_exist"] = _("Invalid game account.")


class CheckoutResponseSerializer(serializers.Serializer):
    order = OrderSerializer()
    payment_url = serializers.URLField(allow_null=True)
