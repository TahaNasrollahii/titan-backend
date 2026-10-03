from django.conf import settings

from rest_framework import serializers

from .models import Payment, Wallet, WalletTransaction


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["balance", "updated_at"]


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = ["id", "amount", "kind", "balance_after", "description", "reference", "created_at"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id",
            "amount",
            "gateway",
            "purpose",
            "object_id",
            "reference",
            "status",
            "ref_id",
            "card_pan",
            "paid_at",
            "created_at",
        ]


class TopupSerializer(serializers.Serializer):
    amount = serializers.IntegerField(
        min_value=settings.WALLET_TOPUP_MIN, max_value=settings.WALLET_TOPUP_MAX
    )


class PaymentStartSerializer(serializers.Serializer):
    """Returned whenever the client must be redirected to the payment gateway."""

    payment_id = serializers.IntegerField(source="payment.pk")
    payment_url = serializers.URLField()
