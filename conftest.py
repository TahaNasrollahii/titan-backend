import pytest
from rest_framework.test import APIClient

from apps.accounts.sms import InMemorySMSBackend
from apps.accounts.tests.factories import RankTierFactory, UserFactory


@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture(autouse=True)
def _clean_sms_outbox():
    InMemorySMSBackend.outbox.clear()
    yield
    InMemorySMSBackend.outbox.clear()


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def staff_user(db):
    return UserFactory(is_staff=True)


@pytest.fixture
def auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def client_for():
    """Build an authenticated client for any user: ``client_for(user)``."""

    def build(user) -> APIClient:
        client = APIClient()
        client.force_authenticate(user)
        return client

    return build


@pytest.fixture
def rank_tiers(db):
    return [
        RankTierFactory(slug="bronze", name="Bronze", min_points=0),
        RankTierFactory(slug="silver", name="Silver", min_points=1500),
        RankTierFactory(slug="gold", name="Gold", min_points=5000),
    ]
