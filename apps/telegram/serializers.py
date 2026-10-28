from rest_framework import serializers

from apps.accounts.serializers import MeSerializer, OTPVerifySerializer, PhoneSerializer

from .initdata import MAX_RAW_LENGTH
from .models import TelegramAccount


class InitDataSerializer(serializers.Serializer):
    init_data = serializers.CharField(max_length=MAX_RAW_LENGTH, trim_whitespace=False)


class ContactLinkSerializer(InitDataSerializer):
    contact = serializers.CharField(max_length=MAX_RAW_LENGTH, required=False, allow_blank=True, default="")


class CodeRequestSerializer(InitDataSerializer, PhoneSerializer):
    pass


class CodeVerifySerializer(InitDataSerializer, OTPVerifySerializer):
    pass


class TelegramAccountSerializer(serializers.ModelSerializer):
    write_access_granted = serializers.BooleanField(write_only=True, required=False)

    class Meta:
        model = TelegramAccount
        fields = [
            "telegram_id",
            "username",
            "first_name",
            "last_name",
            "photo_url",
            "is_premium",
            "notifications_enabled",
            "can_message",
            "write_access_granted",
        ]
        read_only_fields = [f for f in fields if f not in ("notifications_enabled", "write_access_granted")]


class TelegramUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    username = serializers.CharField()
    photo_url = serializers.CharField()


class TelegramSignInSerializer(serializers.Serializer):
    """``linked`` false means: ask the user to share their phone number, then call the contact endpoint."""

    linked = serializers.BooleanField()
    telegram_user = TelegramUserSerializer()
    access = serializers.CharField(required=False)
    refresh = serializers.CharField(required=False)
    is_new_user = serializers.BooleanField(required=False)
    user = MeSerializer(required=False)
    telegram = TelegramAccountSerializer(required=False)
