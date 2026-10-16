import io
from PIL import Image
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def make_test_image(format="PNG", size=(100, 100), color=(255, 0, 0)):
    buf = io.BytesIO()
    img = Image.new("RGBA" if format == "PNG" else "RGB", size, color=color)
    img.save(buf, format=format)
    buf.seek(0)
    return buf.getvalue()


@pytest.fixture
def auth_client():
    user = UserFactory(phone="09121113344")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_avatar_text_disguised_as_png_is_rejected(auth_client):
    fake_png = SimpleUploadedFile("avatar.png", b"This is plain text disguised as PNG.", content_type="image/png")
    response = auth_client.post(reverse("me-avatar"), {"avatar": fake_png}, format="multipart")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "avatar" in response.json()["errors"]


def test_avatar_jpeg_disguised_as_png_is_rejected(auth_client):
    jpeg_bytes = make_test_image(format="JPEG")
    mismatched = SimpleUploadedFile("avatar.png", jpeg_bytes, content_type="image/png")
    response = auth_client.post(reverse("me-avatar"), {"avatar": mismatched}, format="multipart")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "avatar" in response.json()["errors"]


def test_avatar_oversized_dimensions_is_rejected(auth_client):
    large_bytes = make_test_image(format="PNG", size=(2049, 100))
    oversized = SimpleUploadedFile("large.png", large_bytes, content_type="image/png")
    response = auth_client.post(reverse("me-avatar"), {"avatar": oversized}, format="multipart")

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "avatar" in response.json()["errors"]


def test_avatar_valid_image_is_sanitized_and_accepted(auth_client):
    valid_bytes = make_test_image(format="PNG", size=(200, 200))
    valid_png = SimpleUploadedFile("my_avatar.png", valid_bytes, content_type="image/png")
    response = auth_client.post(reverse("me-avatar"), {"avatar": valid_png}, format="multipart")

    assert response.status_code == 200
    data = response.json()
    assert "avatar" in data
    assert data["avatar"] is not None


def test_parse_error_is_sanitized_in_production(settings):
    settings.DEBUG = False
    client = APIClient()

    # Sending invalid raw JSON
    response = client.post(
        reverse("otp-request"),
        data=b"{invalid json payload",
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Invalid request payload.",
        "code": "parse_error",
    }

