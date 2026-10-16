import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import UserFactory
from apps.core.admin_auth import MAX_ADMIN_LOGIN_FAILURES

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user():
    user = UserFactory(phone="09129990000", is_staff=True, is_superuser=True)
    user.set_password("SecureAdminPass123!")
    user.save()
    return user


def test_non_standard_admin_url_works(client):
    admin_slug = getattr(settings, "ADMIN_URL", "manage-titan").strip("/")
    url = f"/{admin_slug}/login/"
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"


def test_default_admin_url_returns_404(client):
    admin_slug = getattr(settings, "ADMIN_URL", "manage-titan").strip("/")
    if admin_slug != "admin":
        response = client.get("/admin/")
        assert response.status_code == 404
        response_login = client.get("/admin/login/")
        assert response_login.status_code == 404


def test_staff_login_requires_2fa(client, staff_user):
    admin_slug = getattr(settings, "ADMIN_URL", "manage-titan").strip("/")
    login_url = f"/{admin_slug}/login/"

    # 1. Attempt login with correct password but missing 2FA code -> should fail
    resp_missing_2fa = client.post(
        login_url,
        {"username": staff_user.phone, "password": "SecureAdminPass123!", "otp_code": ""},
    )
    assert resp_missing_2fa.status_code == 200  # Form re-rendered with errors
    assert "2FA verification code" in resp_missing_2fa.content.decode()

    # 2. Attempt login with wrong 2FA code -> should fail
    resp_wrong_2fa = client.post(
        login_url,
        {"username": staff_user.phone, "password": "SecureAdminPass123!", "otp_code": "000000"},
    )
    assert resp_wrong_2fa.status_code == 200
    assert "Invalid 2FA" in resp_wrong_2fa.content.decode()

    # 3. Attempt login with valid 2FA code ("123456" in test mode) -> should succeed
    resp_valid = client.post(
        login_url,
        {"username": staff_user.phone, "password": "SecureAdminPass123!", "otp_code": "123456"},
    )
    assert resp_valid.status_code == 302  # Redirects into admin dashboard


def test_brute_force_lockout_triggers_after_n_failed_attempts(client, staff_user):
    cache.clear()
    admin_slug = getattr(settings, "ADMIN_URL", "manage-titan").strip("/")
    login_url = f"/{admin_slug}/login/"

    # Fail MAX_ADMIN_LOGIN_FAILURES times
    for _ in range(MAX_ADMIN_LOGIN_FAILURES):
        client.post(
            login_url,
            {"username": staff_user.phone, "password": "WrongPassword!", "otp_code": "123456"},
        )

    # Next attempt must trigger lockout even with correct credentials
    resp_locked = client.post(
        login_url,
        {"username": staff_user.phone, "password": "SecureAdminPass123!", "otp_code": "123456"},
    )
    assert resp_locked.status_code == 200
    assert "temporarily locked" in resp_locked.content.decode()


def test_support_desk_api_strictly_requires_is_staff():
    client = APIClient()

    # 1. Anonymous visitor -> 401 Unauthorized
    resp_anon = client.get("/api/v1/support/desk/agents/")
    assert resp_anon.status_code == 401

    # 2. Authenticated regular (non-staff) user -> 403 Forbidden
    regular_user = UserFactory(phone="09121118888", is_staff=False)
    client.force_authenticate(user=regular_user)
    resp_forbidden = client.get("/api/v1/support/desk/agents/")
    assert resp_forbidden.status_code == 403

    # 3. Authenticated staff user -> 200 OK
    staff = UserFactory(phone="09129997777", is_staff=True)
    client.force_authenticate(user=staff)
    resp_ok = client.get("/api/v1/support/desk/agents/")
    assert resp_ok.status_code == 200

