import importlib
import os
import pytest
from django.core.exceptions import ImproperlyConfigured

pytestmark = pytest.mark.django_db


def test_prod_refuses_fake_payment_gateway(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "api.titan.gg")
    monkeypatch.setenv("SECRET_KEY", "prod-secret-key-test-must-be-very-long-and-secure")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "rG0nTq1Yk1C0D8vMZx0qkqk9V9w0eQn7mQm3QxQb3eA=")
    monkeypatch.setenv("ZARINPAL_MERCHANT_ID", "12345678-1234-1234-1234-123456789012")
    monkeypatch.setenv("PAYMENT_GATEWAY", "fake")
    monkeypatch.setenv("OTP_TEST_CODE", "")
    monkeypatch.setenv("ZARINPAL_SANDBOX", "False")

    with pytest.raises(ImproperlyConfigured, match="PAYMENT_GATEWAY='fake' is strictly forbidden in production."):
        import config.settings.prod
        importlib.reload(config.settings.prod)


def test_prod_refuses_otp_test_code(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "api.titan.gg")
    monkeypatch.setenv("SECRET_KEY", "prod-secret-key-test-must-be-very-long-and-secure")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "rG0nTq1Yk1C0D8vMZx0qkqk9V9w0eQn7mQm3QxQb3eA=")
    monkeypatch.setenv("ZARINPAL_MERCHANT_ID", "12345678-1234-1234-1234-123456789012")
    monkeypatch.setenv("PAYMENT_GATEWAY", "zarinpal")
    monkeypatch.setenv("OTP_TEST_CODE", "123456")
    monkeypatch.setenv("ALLOW_OTP_TEST_CODE", "True")  # even with bypass flag, must fail
    monkeypatch.setenv("ZARINPAL_SANDBOX", "False")

    with pytest.raises(ImproperlyConfigured, match="OTP_TEST_CODE is strictly forbidden in production."):
        import config.settings.prod
        importlib.reload(config.settings.prod)


def test_prod_refuses_zarinpal_sandbox(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "api.titan.gg")
    monkeypatch.setenv("SECRET_KEY", "prod-secret-key-test-must-be-very-long-and-secure")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "rG0nTq1Yk1C0D8vMZx0qkqk9V9w0eQn7mQm3QxQb3eA=")
    monkeypatch.setenv("ZARINPAL_MERCHANT_ID", "12345678-1234-1234-1234-123456789012")
    monkeypatch.setenv("PAYMENT_GATEWAY", "zarinpal")
    monkeypatch.setenv("OTP_TEST_CODE", "")
    monkeypatch.setenv("ZARINPAL_SANDBOX", "True")

    with pytest.raises(ImproperlyConfigured, match="ZARINPAL_SANDBOX=True is strictly forbidden in production."):
        import config.settings.prod
        importlib.reload(config.settings.prod)


def test_prod_refuses_missing_merchant_id(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "api.titan.gg")
    monkeypatch.setenv("SECRET_KEY", "prod-secret-key-test-must-be-very-long-and-secure")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "rG0nTq1Yk1C0D8vMZx0qkqk9V9w0eQn7mQm3QxQb3eA=")
    monkeypatch.setenv("ZARINPAL_MERCHANT_ID", "")
    monkeypatch.setenv("PAYMENT_GATEWAY", "zarinpal")
    monkeypatch.setenv("OTP_TEST_CODE", "")
    monkeypatch.setenv("ZARINPAL_SANDBOX", "False")

    with pytest.raises(ImproperlyConfigured, match="A valid ZARINPAL_MERCHANT_ID must be configured in production."):
        import config.settings.prod
        importlib.reload(config.settings.prod)

