from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

import pytest

from apps.accounts.models import OTPCode, User
from apps.accounts.sms import InMemorySMSBackend
from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PHONE = "09121112233"


def request_code(client, phone=PHONE):
    return client.post(reverse("otp-request"), {"phone": phone}, format="json")


def verify(client, code, phone=PHONE):
    return client.post(reverse("otp-verify"), {"phone": phone, "code": code}, format="json")


class TestOTPRequest:
    def test_sends_code_and_reports_timing(self, api_client):
        response = request_code(api_client)

        assert response.status_code == 201
        assert response.json() == {"phone": PHONE, "expiresIn": 120, "resendIn": 60}
        assert InMemorySMSBackend.last_code_for(PHONE) is not None

    @pytest.mark.parametrize("raw", ["+989121112233", "00989121112233", "9121112233", "۰۹۱۲۱۱۱۲۲۳۳"])
    def test_normalizes_phone_formats(self, api_client, raw):
        response = request_code(api_client, raw)

        assert response.status_code == 201
        assert response.json()["phone"] == PHONE

    def test_rejects_invalid_phone(self, api_client):
        response = request_code(api_client, "12345")

        assert response.status_code == 400
        assert response.json()["code"] == "validation_error"
        assert "phone" in response.json()["errors"]

    def test_code_is_stored_hashed(self, api_client):
        request_code(api_client)
        code = InMemorySMSBackend.last_code_for(PHONE)

        assert OTPCode.objects.get().code_hash != code

    def test_resend_cooldown(self, api_client):
        request_code(api_client)
        response = request_code(api_client)

        assert response.status_code == 429
        assert response.json()["code"] == "otp_throttled"

    def test_hourly_limit(self, api_client, settings):
        settings.OTP_RESEND_COOLDOWN_SECONDS = 0
        settings.OTP_MAX_PER_HOUR = 2
        assert request_code(api_client).status_code == 201
        assert request_code(api_client).status_code == 201

        assert request_code(api_client).status_code == 429


class TestOTPVerify:
    def test_registers_new_user_and_returns_tokens(self, api_client):
        request_code(api_client)

        response = verify(api_client, InMemorySMSBackend.last_code_for(PHONE))

        body = response.json()
        assert response.status_code == 200
        assert body["isNewUser"] is True
        assert body["access"] and body["refresh"]
        assert body["user"]["phone"] == PHONE
        user = User.objects.get(phone=PHONE)
        assert not user.has_usable_password()

    def test_logs_in_existing_user(self, api_client):
        UserFactory(phone=PHONE)
        request_code(api_client)

        response = verify(api_client, InMemorySMSBackend.last_code_for(PHONE))

        assert response.json()["isNewUser"] is False
        assert User.objects.filter(phone=PHONE).count() == 1

    def test_access_token_authenticates(self, api_client):
        request_code(api_client)
        access = verify(api_client, InMemorySMSBackend.last_code_for(PHONE)).json()["access"]

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        assert api_client.get(reverse("me")).status_code == 200

    def test_wrong_code_counts_attempts(self, api_client):
        request_code(api_client)

        response = verify(
            api_client, "00000" if InMemorySMSBackend.last_code_for(PHONE) != "00000" else "11111"
        )

        assert response.status_code == 400
        assert response.json()["code"] == "invalid_otp"
        assert OTPCode.objects.get().attempts == 1

    def test_locks_after_max_attempts(self, api_client, settings):
        settings.OTP_MAX_ATTEMPTS = 2
        request_code(api_client)
        code = InMemorySMSBackend.last_code_for(PHONE)
        wrong = "00000" if code != "00000" else "11111"
        verify(api_client, wrong)
        verify(api_client, wrong)

        response = verify(api_client, code)

        assert response.status_code == 429
        assert response.json()["code"] == "otp_attempts_exceeded"

    def test_expired_code_is_rejected(self, api_client):
        request_code(api_client)
        OTPCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        response = verify(api_client, InMemorySMSBackend.last_code_for(PHONE))

        assert response.json()["code"] == "invalid_otp"

    def test_code_cannot_be_reused(self, api_client):
        request_code(api_client)
        code = InMemorySMSBackend.last_code_for(PHONE)
        assert verify(api_client, code).status_code == 200

        assert verify(api_client, code).status_code == 400

    def test_persian_digits_in_code_are_accepted(self, api_client):
        request_code(api_client)
        code = InMemorySMSBackend.last_code_for(PHONE)
        persian = code.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

        assert verify(api_client, persian).status_code == 200

    def test_disabled_account_cannot_log_in(self, api_client):
        UserFactory(phone=PHONE, is_active=False)
        request_code(api_client)

        response = verify(api_client, InMemorySMSBackend.last_code_for(PHONE))

        assert response.status_code == 403
        assert response.json()["code"] == "account_disabled"


class TestTokens:
    def _login(self, client):
        request_code(client)
        return verify(client, InMemorySMSBackend.last_code_for(PHONE)).json()

    def test_refresh_rotates_token(self, api_client):
        tokens = self._login(api_client)

        response = api_client.post(reverse("token-refresh"), {"refresh": tokens["refresh"]}, format="json")

        assert response.status_code == 200
        assert response.json()["refresh"] != tokens["refresh"]

    def test_logout_blacklists_refresh_token(self, api_client):
        tokens = self._login(api_client)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        assert (
            api_client.post(reverse("logout"), {"refresh": tokens["refresh"]}, format="json").status_code
            == 204
        )
        api_client.credentials()
        response = api_client.post(reverse("token-refresh"), {"refresh": tokens["refresh"]}, format="json")

        assert response.status_code == 401

    def test_logout_with_garbage_token(self, auth_client):
        response = auth_client.post(reverse("logout"), {"refresh": "not-a-token"}, format="json")

        assert response.status_code == 400
        assert response.json()["code"] == "invalid_token"


class TestFixedTestCode:
    def test_fixed_code_logs_in(self, api_client, settings):
        settings.OTP_TEST_CODE = "12345"
        request_code(api_client)

        response = verify(api_client, "12345")

        assert response.status_code == 200
        assert InMemorySMSBackend.last_code_for(PHONE) == "12345"

    def test_production_refuses_fixed_code(self, monkeypatch):
        import importlib

        from django.core.exceptions import ImproperlyConfigured

        monkeypatch.setenv("OTP_TEST_CODE", "12345")
        monkeypatch.setenv("ALLOWED_HOSTS", "example.com")
        with pytest.raises(ImproperlyConfigured):
            importlib.import_module("config.settings.prod")
