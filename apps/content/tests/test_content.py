from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

import pytest

from apps.catalog.tests.factories import GameFactory, ProductFactory
from apps.content.models import Announcement, ContactChannel, Guide, Promo, SiteSetting

pytestmark = pytest.mark.django_db


def test_promos_respect_active_window_and_placement(api_client):
    now = timezone.now()
    product = ProductFactory(slug="vbucks")
    Promo.objects.create(
        placement="store_discount", title="live", product=product, ends_at=now + timedelta(hours=2)
    )
    Promo.objects.create(placement="store_discount", title="expired", ends_at=now - timedelta(minutes=1))
    Promo.objects.create(placement="store_discount", title="future", starts_at=now + timedelta(days=1))
    Promo.objects.create(placement="store_discount", title="disabled", is_active=False)
    Promo.objects.create(placement="home_hero", title="other placement")

    body = api_client.get(reverse("promo-list"), {"placement": "store_discount"}).json()

    assert [promo["title"] for promo in body] == ["live"]
    assert body[0]["product"] == "vbucks"
    assert body[0]["endsAt"] is not None


def test_announcements(api_client):
    Announcement.objects.create(title="Cup", text="Registration closing", icon="trophy")
    Announcement.objects.create(title="Old", text="x", is_active=False)

    assert [a["title"] for a in api_client.get(reverse("announcement-list")).json()] == ["Cup"]


def test_contact_info(api_client):
    ContactChannel.objects.create(
        kind="discord", title="Discord", url="https://discord.gg/x", is_primary=True
    )
    ContactChannel.objects.create(kind="email", title="Hidden", url="mailto:x@y.z", is_active=False)
    SiteSetting.objects.create(key="support_online", value=False)

    body = api_client.get(reverse("contact-info")).json()

    assert body["supportOnline"] is False
    assert [channel["title"] for channel in body["channels"]] == ["Discord"]


def test_support_online_defaults_to_true(api_client):
    assert api_client.get(reverse("contact-info")).json()["supportOnline"] is True


def _guide(slug, *, published=True, **fields):
    defaults = {"title": slug, "category": "purchase", "summary": "s", "body": "b", "is_published": published}
    return Guide.objects.create(slug=slug, **{**defaults, **fields})


def test_guides_list_only_published_and_filters(api_client):
    product = ProductFactory(slug="vp-test", game=GameFactory(slug="valorant-test"))
    vp = _guide("vp-guide", game=product.game)
    vp.products.add(product)
    _guide("wallet-guide", category="site")
    _guide("draft-guide", published=False)

    titles = lambda **params: {g["slug"] for g in api_client.get(reverse("guide-list"), params).json()}

    assert titles() >= {"vp-guide", "wallet-guide"}
    assert "draft-guide" not in titles()
    assert titles(category="site") == {"wallet-guide"}
    assert titles(product="vp-test") == {"vp-guide"}
    assert titles(game=product.game.slug) == {"vp-guide"}


def test_guide_detail_hides_drafts_and_lists_related(api_client):
    guide = _guide("detail-guide")
    guide.faqs.create(question="Q?", answer="A.")
    _guide("sibling-guide")
    _guide("hidden-guide", published=False)

    body = api_client.get(reverse("guide-detail", args=["detail-guide"])).json()

    assert body["faqs"] == [{"question": "Q?", "answer": "A."}]
    assert body["publishedAt"] is not None
    assert "sibling-guide" in {g["slug"] for g in body["related"]}
    assert "hidden-guide" not in {g["slug"] for g in body["related"]}
    assert api_client.get(reverse("guide-detail", args=["hidden-guide"])).status_code == 404


def test_published_guides_are_in_the_sitemap(api_client):
    _guide("sitemap-guide")
    _guide("sitemap-draft", published=False)

    slugs = {g["slug"] for g in api_client.get(reverse("seo-sitemap")).json()["guides"]}

    assert "sitemap-guide" in slugs
    assert "sitemap-draft" not in slugs
