"""Fixed-price products vs products with options (e.g. V-Bucks 1,000 / 2,800 / 5,000)."""

from django.urls import reverse

import pytest

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.orders import services as order_services
from apps.payments.models import WalletTransaction
from apps.payments.services import credit

pytestmark = pytest.mark.django_db

PRODUCTS_URL = reverse("product-list")


@pytest.fixture
def vbucks():
    product = ProductFactory(slug="vbucks", price=None)
    ProductVariantFactory(product=product, label="1,000", price=299_000, original_price=350_000, stock=10)
    ProductVariantFactory(product=product, label="2,800", price=749_000, stock=4)
    ProductVariantFactory(product=product, label="13,500", price=2_999_000, stock=1)
    product.refresh_from_db()
    return product


class TestOptionSummary:
    def test_price_range_and_stock_follow_options(self, vbucks):
        assert vbucks.has_variants is True
        assert (vbucks.price, vbucks.original_price, vbucks.price_max) == (299_000, 350_000, 2_999_000)
        assert vbucks.stock == 15

    def test_unlimited_option_makes_product_unlimited(self, vbucks):
        ProductVariantFactory(product=vbucks, label="unlimited", stock=None)

        vbucks.refresh_from_db()
        assert vbucks.stock is None

    def test_editing_an_option_updates_the_product(self, vbucks):
        cheapest = vbucks.variants.get(label="1,000")
        cheapest.price = 999_000
        cheapest.original_price = None
        cheapest.save()

        vbucks.refresh_from_db()
        assert (vbucks.price, vbucks.original_price) == (749_000, None)

    def test_removing_all_options_makes_it_fixed_price(self, vbucks):
        vbucks.variants.all().delete()  # queryset delete still sends post_delete per row

        vbucks.refresh_from_db()
        assert (vbucks.has_variants, vbucks.price_max) == (False, None)

    def test_fixed_price_product_is_untouched(self):
        crew_pack = ProductFactory(price=990_000, original_price=1_100_000)

        assert (crew_pack.has_variants, crew_pack.price, crew_pack.price_max) == (False, 990_000, None)


class TestApi:
    def test_list_distinguishes_fixed_and_option_products(self, api_client, vbucks):
        ProductFactory(slug="crew-pack", price=990_000)

        items = {item["slug"]: item for item in api_client.get(PRODUCTS_URL).json()["results"]}

        assert (items["vbucks"]["hasVariants"], items["vbucks"]["price"], items["vbucks"]["priceMax"]) == (
            True,
            299_000,
            2_999_000,
        )
        assert (items["crew-pack"]["hasVariants"], items["crew-pack"]["priceMax"]) == (False, None)

    def test_price_sort_and_filter_use_starting_price(self, api_client, vbucks):
        ProductFactory(slug="crew-pack", price=990_000)

        ordered = [
            item["slug"] for item in api_client.get(PRODUCTS_URL, {"ordering": "price"}).json()["results"]
        ]
        cheap = [
            item["slug"] for item in api_client.get(PRODUCTS_URL, {"price_max": 500_000}).json()["results"]
        ]

        assert ordered == ["vbucks", "crew-pack"]
        assert cheap == ["vbucks"]

    def test_detail_lists_each_option_with_its_price(self, api_client, vbucks):
        body = api_client.get(f"{PRODUCTS_URL}vbucks/").json()

        assert [(v["label"], v["price"], v["inStock"]) for v in body["variants"]] == [
            ("1,000", 299_000, True),
            ("2,800", 749_000, True),
            ("13,500", 2_999_000, True),
        ]


class TestPurchasing:
    def test_option_is_required_and_priced_individually(self, user, vbucks):
        option = vbucks.variants.get(label="2,800")

        item = order_services.add_to_cart(user, vbucks, option, quantity=2)

        assert (item.unit_price, item.line_total) == (749_000, 1_498_000)

    def test_fixed_price_product_needs_no_option(self, user):
        crew_pack = ProductFactory(price=990_000)

        assert order_services.add_to_cart(user, crew_pack).unit_price == 990_000

    def test_selling_an_option_updates_product_stock(self, user, vbucks):
        option = vbucks.variants.get(label="13,500")
        order_services.add_to_cart(user, vbucks, option)
        credit(user, 3_000_000, kind=WalletTransaction.Kind.TOPUP)

        order_services.checkout(user, payment_method="wallet")

        option.refresh_from_db()
        vbucks.refresh_from_db()
        assert option.stock == 0
        assert vbucks.stock == 14

    def test_product_without_price_or_options_cannot_be_bought(self, auth_client):
        broken = ProductFactory(price=None)

        response = auth_client.post(reverse("cart-items"), {"product": broken.slug}, format="json")

        assert response.json()["code"] == "product_unavailable"


class TestAdmin:
    def _post(self, client, **overrides):
        data = {
            "slug": "new",
            "title": "New",
            "category": ProductFactory().category_id,
            "delivery_type": "code",
            "price": "",
            "popularity": 0,
            "features": "[]",
            "tags": "[]",
            "specs": "{}",
            "is_active": "on",
            "variants-TOTAL_FORMS": 0,
            "variants-INITIAL_FORMS": 0,
            "gallery-TOTAL_FORMS": 0,
            "gallery-INITIAL_FORMS": 0,
            **overrides,
        }
        return client.post(reverse("admin:catalog_product_add"), data)

    @pytest.fixture
    def admin_client(self, client):
        from apps.accounts.tests.factories import UserFactory

        client.force_login(UserFactory(is_staff=True, is_superuser=True))
        return client

    def test_needs_price_or_options(self, admin_client):
        response = self._post(admin_client)

        assert response.status_code == 200  # form re-rendered with the error
        assert not Product.objects.filter(slug="new").exists()

    def test_product_with_options_only(self, admin_client):
        response = self._post(
            admin_client,
            **{
                "variants-TOTAL_FORMS": 2,
                "variants-0-label": "Small",
                "variants-0-price": 100,
                "variants-0-sort_order": 0,
                "variants-1-label": "Big",
                "variants-1-price": 300,
                "variants-1-sort_order": 1,
            },
        )

        assert response.status_code == 302
        product = Product.objects.get(slug="new")
        assert (product.has_variants, product.price, product.price_max) == (True, 100, 300)

    def test_fixed_price_product(self, admin_client):
        assert self._post(admin_client, price=990_000).status_code == 302
        assert Product.objects.get(slug="new").price == 990_000
