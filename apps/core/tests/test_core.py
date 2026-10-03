from django.core.exceptions import ValidationError
from django.urls import reverse

import pytest
from rest_framework import serializers

from apps.core.fields import decrypt, encrypt
from apps.core.utils import normalize_digits, normalize_phone, percent_off
from apps.core.validators import MaxFileSizeValidator


class TestPhone:
    @pytest.mark.parametrize(
        "raw",
        ["09121234567", "+98 912 123 4567", "00989121234567", "989121234567", "9121234567", "۰۹۱۲۱۲۳۴۵۶۷"],
    )
    def test_normalizes(self, raw):
        assert normalize_phone(raw) == "09121234567"

    @pytest.mark.parametrize("raw", ["", "0912", "02112345678", "091212345678", "abc"])
    def test_rejects(self, raw):
        with pytest.raises(serializers.ValidationError):
            normalize_phone(raw)


def test_normalize_digits():
    assert normalize_digits("۱۲۳٤٥") == "12345"


@pytest.mark.parametrize(
    ("price", "original", "expected"), [(75, 100, 25), (100, None, 0), (100, 100, 0), (120, 100, 0)]
)
def test_percent_off(price, original, expected):
    assert percent_off(price, original) == expected


def test_encryption_round_trip():
    token = encrypt("secret")

    assert token != "secret"
    assert decrypt(token) == "secret"


def test_decrypt_with_wrong_key_fails_loudly():
    with pytest.raises(ValueError, match="FIELD_ENCRYPTION_KEY"):
        decrypt("not-a-token")


def test_max_file_size_validator():
    class FakeFile:
        size = 11

    with pytest.raises(ValidationError):
        MaxFileSizeValidator(10)(FakeFile())
    MaxFileSizeValidator(11)(FakeFile())


@pytest.mark.django_db
class TestApiConventions:
    def test_health(self, api_client):
        assert api_client.get(reverse("health")).json() == {"status": "ok", "database": "ok"}

    def test_not_found_shape(self, api_client):
        body = api_client.get(reverse("product-detail", args=["missing"])).json()

        assert body["code"] == "not_found"
        assert body["detail"]

    def test_validation_error_shape(self, api_client):
        body = api_client.post(reverse("otp-request"), {}, format="json").json()

        assert body["code"] == "validation_error"
        assert "phone" in body["errors"]

    def test_messages_are_persian_by_default(self, api_client):
        body = api_client.get(reverse("me")).json()

        assert body["code"] == "not_authenticated"
        assert body["detail"] == "اطلاعات برای اعتبارسنجی ارسال نشده است."

    def test_english_via_accept_language(self, api_client):
        body = api_client.post(reverse("otp-request"), {"phone": "1"}, HTTP_ACCEPT_LANGUAGE="en").json()

        assert body["detail"] == "Invalid input."

    def test_schema_is_served(self, api_client):
        assert api_client.get(reverse("schema")).status_code == 200
