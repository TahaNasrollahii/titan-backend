from django.core.exceptions import ValidationError
from django.urls import reverse

import pytest

from apps.catalog.tests.factories import GameFactory, ProductCategoryFactory, ProductFactory
from apps.core.models import Redirect
from apps.tournaments.models import Tournament
from apps.tournaments.tests.factories import TournamentFactory

pytestmark = pytest.mark.django_db


class TestRedirectModelAndSignals:
    def test_normalizes_paths_and_rejects_self_redirect(self):
        redirect_obj = Redirect.objects.create(old_path="product/old/", new_path="product/new/")
        assert redirect_obj.old_path == "/product/old"
        assert redirect_obj.new_path == "/product/new"

        with pytest.raises(ValidationError):
            Redirect.objects.create(old_path="/product/same/", new_path="/product/same")

    def test_flattens_chains_and_prevents_loops_on_slug_change(self):
        product = ProductFactory(slug="v-bucks-1000", title="1000 V-Bucks")

        product.slug = "vbucks-1000"
        product.save()

        r1 = Redirect.objects.get(old_path="/product/v-bucks-1000")
        assert r1.new_path == "/product/vbucks-1000"
        assert r1.is_permanent is True

        # Second rename: chain /product/v-bucks-1000 -> /product/vbucks-1000 -> /product/fortnite-vbucks-1000
        # must flatten so both point directly to /product/fortnite-vbucks-1000
        product.slug = "fortnite-vbucks-1000"
        product.save()

        r1.refresh_from_db()
        r2 = Redirect.objects.get(old_path="/product/vbucks-1000")
        assert r1.new_path == "/product/fortnite-vbucks-1000"
        assert r2.new_path == "/product/fortnite-vbucks-1000"

        # Rename back to original slug: must remove outgoing redirect from /product/v-bucks-1000 and avoid loops
        product.slug = "v-bucks-1000"
        product.save()

        assert not Redirect.objects.filter(old_path="/product/v-bucks-1000").exists()
        assert Redirect.objects.get(old_path="/product/vbucks-1000").new_path == "/product/v-bucks-1000"
        assert Redirect.objects.get(old_path="/product/fortnite-vbucks-1000").new_path == "/product/v-bucks-1000"

    def test_game_and_tournament_slug_changes_record_redirects(self):
        game = GameFactory(slug="fc-24")
        game.slug = "ea-sports-fc-24"
        game.save()
        assert Redirect.objects.get(old_path="/store/fc-24").new_path == "/store/ea-sports-fc-24"

        tournament = TournamentFactory(slug="spring-cup")
        tournament.slug = "spring-cup-2026"
        tournament.save()
        assert Redirect.objects.get(old_path="/tournaments/spring-cup").new_path == "/tournaments/spring-cup-2026"


class TestSeoSerializersAndEndpoints:
    def test_product_and_game_seo_fallbacks_and_overrides(self, api_client):
        game = GameFactory(
            slug="valorant",
            title="ولورنت",
            title_en="Valorant",
            intro_text="خرید فوری پوینت ولورنت با تحویل آنی.",
            bottom_seo_text="راهنمای کامل خرید VP ولورنت در ایران.",
        )
        product = ProductFactory(
            slug="valorant-1000-vp",
            title="۱۰۰۰ پوینت ولورنت",
            game=game,
            gtin="0123456789012",
            mpn="VAL-1000",
        )

        game_data = api_client.get(f"/api/v1/games/{game.slug}/").json()
        assert "ولورنت" in game_data["metaTitle"]
        assert "پوینت ولورنت" in game_data["metaDescription"]
        assert game_data["introText"] == "خرید فوری پوینت ولورنت با تحویل آنی."
        assert game_data["bottomSeoText"] == "راهنمای کامل خرید VP ولورنت در ایران."
        assert game_data["noindex"] is False

        prod_data = api_client.get(f"/api/v1/products/{product.slug}/").json()
        assert prod_data["sku"] == f"TITAN-{product.id}"
        assert prod_data["gtin"] == "0123456789012"
        assert prod_data["mpn"] == "VAL-1000"
        assert prod_data["imageAlt"] == "۱۰۰۰ پوینت ولورنت"
        assert "۱۰۰۰ پوینت ولورنت" in prod_data["metaTitle"]
        assert prod_data["metaDescription"] != ""

    def test_sitemap_endpoint_filters_noindex_inactive_and_non_canonical(self, api_client):
        GameFactory(slug="indexed-game", is_active=True, noindex=False)
        GameFactory(slug="noindex-game", is_active=True, noindex=True)
        GameFactory(slug="inactive-game", is_active=False, noindex=False)

        ProductCategoryFactory(slug="indexed-cat", noindex=False)
        ProductCategoryFactory(slug="noindex-cat", noindex=True)

        ProductFactory(slug="indexed-prod", is_active=True, noindex=False)
        ProductFactory(slug="noindex-prod", is_active=True, noindex=True)
        ProductFactory(slug="canonical-override", is_active=True, canonical_url="https://example.com/other")

        TournamentFactory(slug="indexed-tourney", noindex=False, state=Tournament.State.SCHEDULED)
        TournamentFactory(slug="cancelled-tourney", noindex=False, state=Tournament.State.CANCELLED)
        TournamentFactory(slug="noindex-tourney", noindex=True, state=Tournament.State.LIVE)

        res = api_client.get(reverse("seo-sitemap"))
        assert res.status_code == 200
        body = res.json()

        game_slugs = {g["slug"] for g in body["games"]}
        assert "indexed-game" in game_slugs
        assert "noindex-game" not in game_slugs
        assert "inactive-game" not in game_slugs

        cat_slugs = {c["slug"] for c in body["categories"]}
        assert "indexed-cat" in cat_slugs
        assert "noindex-cat" not in cat_slugs

        prod_slugs = {p["slug"] for p in body["products"]}
        assert "indexed-prod" in prod_slugs
        assert "noindex-prod" not in prod_slugs
        assert "canonical-override" not in prod_slugs

        tourney_slugs = {t["slug"] for t in body["tournaments"]}
        assert "indexed-tourney" in tourney_slugs
        assert "cancelled-tourney" not in tourney_slugs
        assert "noindex-tourney" not in tourney_slugs

    def test_redirects_endpoint_lookup(self, api_client):
        Redirect.objects.create(old_path="/product/old-card", new_path="/product/new-card", is_permanent=True)

        all_res = api_client.get(reverse("seo-redirects"))
        assert all_res.status_code == 200
        assert "/product/old-card" in all_res.json()["map"]

        found_res = api_client.get(reverse("seo-redirects"), {"path": "/product/old-card/"})
        assert found_res.status_code == 200
        assert found_res.json()["newPath"] == "/product/new-card"
        assert found_res.json()["isPermanent"] is True

        missing_res = api_client.get(reverse("seo-redirects"), {"path": "/product/unknown"})
        assert missing_res.status_code == 404
