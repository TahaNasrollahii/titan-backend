from unittest.mock import patch
import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from apps.core.turnstile import validate_turnstile, verify_turnstile_token

pytestmark = pytest.mark.django_db


def test_turnstile_passes_when_key_not_configured_in_test(settings):
    settings.IS_TESTING = True
    settings.TURNSTILE_SECRET_KEY = ""
    assert verify_turnstile_token("") is True


def test_turnstile_fails_when_key_configured_and_token_missing(settings):
    settings.IS_TESTING = False
    settings.DEBUG = False
    settings.TURNSTILE_SECRET_KEY = "0x4AAAAAAtestsecret"
    assert verify_turnstile_token("") is False


def test_turnstile_calls_cloudflare_and_validates(settings):
    settings.IS_TESTING = False
    settings.DEBUG = False
    settings.TURNSTILE_SECRET_KEY = "0x4AAAAAAtestsecret"

    with patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = {"success": True}
        mock_post.return_value.status_code = 200
        assert verify_turnstile_token("sample-token", ip="1.2.3.4") is True

    with patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = {"success": False, "error-codes": ["invalid-input-response"]}
        mock_post.return_value.status_code = 200
        assert verify_turnstile_token("invalid-token", ip="1.2.3.4") is False


def test_validate_turnstile_raises_validation_error_on_failure(settings):
    settings.IS_TESTING = False
    settings.DEBUG = False
    settings.TURNSTILE_SECRET_KEY = "0x4AAAAAAtestsecret"

    factory = APIRequestFactory()
    request = factory.post("/api/v1/auth/otp/request/", {"phone": "09121112233"}, format="json")

    with pytest.raises(ValidationError, match="Security verification failed"):
        validate_turnstile(request)

