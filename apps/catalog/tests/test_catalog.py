from django.urls import reverse

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    GameFactory,
    PlatformFactory,
    ProductCategoryFactory,
    ProductFactory,
    ProductVariantFactory,
)
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db

PRODUCTS_URL = reverse("product-list")
AUTHOR = {"authorName": "Taha", "authorEmail": "taha@example.com"}


def product_url(product, suffix=""):
    return f"{PRODUCTS_URL}{product.slug}/{suffix}"


def slugs(response):
    return [item["slug"] for item in response.json()["results"]]


class TestGames:
    def test_list_with_counts(self, api_client):
        game = GameFactory(slug="fortnite", is_featured=True)
        ProductFactory.create_batch(2, game=game)
        ProductFactory(game=game, is_active=False)
        UserFactory(favorite_game=game)

        body = api_client.get(reverse("game-list")).json()

        assert body[0]["slug"] == "fortnite"
        assert body[0]["productCount"] == 2
        assert body[0]["playerCount"] == 1
        assert body[0]["tournamentCount"] == 0

    def test_detail_by_slug(self, api_client):
        GameFactory(slug="valorant", title_en="Valorant")

        assert api_client.get(reverse("game-detail", args=["valorant"])).json()["titleEn"] == "Valorant"

    def test_filter_featured(self, api_client):
        GameFactory(slug="a", is_featured=True)
        GameFactory(slug="b", is_featured=False)

        body = api_client.get(reverse("game-list"), {"is_featured": "true"}).json()

        assert [game["slug"] for game in body] == ["a"]


class TestProductList:
    def test_filters(self, api_client):
        fortnite = GameFactory(slug="fortnite")
        pc = PlatformFactory(slug="pc")
        gift = ProductCategoryFactory(slug="gift-card")
        match = ProductFactory(slug="match", game=fortnite, category=gift, price=200_000)
        match.platforms.add(pc)
        ProductFactory(slug="other-game", category=gift, price=200_000)
        ProductFactory(slug="too-expensive", game=fortnite, category=gift, price=9_000_000)

        response = api_client.get(
            PRODUCTS_URL,
            {"game": "fortnite", "category": "gift-card", "platform": "pc", "price_max": 1_000_000},
        )

        assert slugs(response) == ["match"]

    def test_hides_inactive_products(self, api_client):
        ProductFactory(slug="visible")
        ProductFactory(slug="hidden", is_active=False)

        assert slugs(api_client.get(PRODUCTS_URL)) == ["visible"]

    @pytest.mark.parametrize(
        ("ordering", "expected"),
        [("price", ["cheap", "mid", "pricey"]), ("-price", ["pricey", "mid", "cheap"])],
    )
    def test_price_ordering(self, api_client, ordering, expected):
        ProductFactory(slug="mid", price=200)
        ProductFactory(slug="cheap", price=100)
        ProductFactory(slug="pricey", price=300)

        assert slugs(api_client.get(PRODUCTS_URL, {"ordering": ordering})) == expected

    def test_default_ordering_is_popularity(self, api_client):
        ProductFactory(slug="meh", popularity=1)
        ProductFactory(slug="hot", popularity=99)

        assert slugs(api_client.get(PRODUCTS_URL)) == ["hot", "meh"]

    def test_search(self, api_client):
        ProductFactory(slug="vbucks", title="ویباکس فورتنایت")
        ProductFactory(slug="steam", title="Steam wallet")

        assert slugs(api_client.get(PRODUCTS_URL, {"search": "ویباکس"})) == ["vbucks"]

    def test_on_sale_and_stock_filters(self, api_client):
        ProductFactory(slug="sale", price=80, original_price=100)
        ProductFactory(slug="sold-out", stock=0)

        assert slugs(api_client.get(PRODUCTS_URL, {"on_sale": "true"})) == ["sale"]
        assert slugs(api_client.get(PRODUCTS_URL, {"in_stock": "false"})) == ["sold-out"]

    def test_badges_and_discount(self, api_client):
        ProductFactory(slug="deal", price=75, original_price=100, is_bestseller=True, is_new=True)

        item = api_client.get(PRODUCTS_URL).json()["results"][0]

        assert item["discountPercent"] == 25
        assert item["badges"] == ["bestseller", "discount", "new"]

    def test_pagination(self, api_client):
        ProductFactory.create_batch(15)

        body = api_client.get(PRODUCTS_URL, {"page_size": 10}).json()

        assert body["count"] == 15
        assert len(body["results"]) == 10
        assert body["next"]

    def test_query_count_is_constant(self, api_client, django_assert_max_num_queries):
        for _ in range(10):
            product = ProductFactory(game=GameFactory())
            product.platforms.add(PlatformFactory())

        with django_assert_max_num_queries(4):
            api_client.get(PRODUCTS_URL)

    def test_is_wishlisted_flag(self, auth_client, user):
        liked = ProductFactory(slug="liked")
        ProductFactory(slug="not-liked")
        user.wishlist.create(product=liked)

        flags = {
            item["slug"]: item["isWishlisted"] for item in auth_client.get(PRODUCTS_URL).json()["results"]
        }

        assert flags == {"liked": True, "not-liked": False}


class TestProductDetail:
    def test_detail_includes_variants(self, api_client):
        product = ProductFactory(delivery_type=Product.DeliveryType.ACCOUNT)
        ProductVariantFactory(product=product, label="Small", price=10, sort_order=1)
        ProductVariantFactory(product=product, label="Big", price=20, sort_order=2)

        body = api_client.get(product_url(product)).json()

        assert [variant["label"] for variant in body["variants"]] == ["Small", "Big"]
        assert body["requiresGameAccount"] is True

    def test_inactive_product_404(self, api_client):
        product = ProductFactory(is_active=False)

        assert api_client.get(product_url(product)).status_code == 404

    def test_related_products(self, api_client):
        game = GameFactory()
        product = ProductFactory(game=game)
        sibling = ProductFactory(game=game)
        unrelated = ProductFactory()  # different category & game

        related = [item["slug"] for item in api_client.get(product_url(product, "related/")).json()]

        assert sibling.slug in related
        assert unrelated.slug not in related
        assert product.slug not in related


class TestReviews:
    def test_anyone_can_read_reviews(self, api_client):
        product = ProductFactory()

        assert api_client.get(product_url(product, "reviews/")).status_code == 200

    def test_submit_updates_aggregates(self, client_for):
        product = ProductFactory()
        client_for(UserFactory()).post(
            product_url(product, "reviews/"), {**AUTHOR, "rating": 5, "comment": "Great"}
        )
        client_for(UserFactory()).post(product_url(product, "reviews/"), {**AUTHOR, "rating": 4})

        product.refresh_from_db()
        assert (float(product.rating_avg), product.review_count) == (4.5, 2)

    def test_second_review_updates_first(self, auth_client):
        product = ProductFactory()
        auth_client.post(product_url(product, "reviews/"), {**AUTHOR, "rating": 1})

        auth_client.post(product_url(product, "reviews/"), {**AUTHOR, "rating": 5})

        product.refresh_from_db()
        assert (float(product.rating_avg), product.review_count) == (5.0, 1)

    def test_verified_purchase(self, auth_client, user):
        product = ProductFactory()
        order = Order.objects.create(
            number="TTN-1",
            user=user,
            status=Order.Status.COMPLETED,
            payment_method="wallet",
            subtotal=1,
            total=1,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            title="x",
            delivery_type="code",
            unit_price=1,
            unit_original_price=1,
            quantity=1,
            line_total=1,
        )

        body = auth_client.post(product_url(product, "reviews/"), {**AUTHOR, "rating": 5}).json()

        assert body["verifiedPurchase"] is True

    def test_name_and_email_required(self, auth_client):
        product = ProductFactory()

        body = auth_client.post(product_url(product, "reviews/"), {"rating": 5}).json()

        assert body["code"] == "validation_error"
        assert set(body["errors"]) == {"authorName", "authorEmail"}

    def test_invalid_email_rejected(self, auth_client):
        product = ProductFactory()
        payload = {**AUTHOR, "authorEmail": "not-an-email", "rating": 5}

        assert auth_client.post(product_url(product, "reviews/"), payload).status_code == 400

    def test_author_name_public_email_private(self, auth_client, api_client):
        product = ProductFactory()
        created = auth_client.post(
            product_url(product, "reviews/"), {**AUTHOR, "rating": 4, "comment": "خوب"}
        ).json()

        listed = api_client.get(product_url(product, "reviews/")).json()["results"][0]

        for review in (created, listed):
            assert review["authorName"] == "Taha"
            assert "authorEmail" not in review

    def test_rating_bounds(self, auth_client):
        product = ProductFactory()

        assert auth_client.post(product_url(product, "reviews/"), {**AUTHOR, "rating": 6}).status_code == 400

    def test_anonymous_cannot_review(self, api_client):
        product = ProductFactory()

        assert api_client.post(product_url(product, "reviews/"), {**AUTHOR, "rating": 5}).status_code == 401

    def test_delete_own_review(self, auth_client):
        product = ProductFactory()
        auth_client.post(product_url(product, "reviews/"), {**AUTHOR, "rating": 5})

        assert auth_client.delete(product_url(product, "reviews/mine/")).status_code == 204
        product.refresh_from_db()
        assert product.review_count == 0


class TestWishlist:
    url = reverse("wishlist-list")

    def test_add_list_remove(self, auth_client):
        product = ProductFactory(slug="want")

        assert auth_client.post(self.url, {"product": "want"}).status_code == 201
        assert auth_client.post(self.url, {"product": "want"}).status_code == 201  # idempotent
        listed = auth_client.get(self.url).json()["results"]
        assert [(item["product"]["slug"], item["product"]["isWishlisted"]) for item in listed] == [
            ("want", True)
        ]

        assert auth_client.delete(f"{self.url}{product.slug}/").status_code == 204
        assert auth_client.get(self.url).json()["results"] == []

    def test_requires_auth(self, api_client):
        assert api_client.get(self.url).status_code == 401
