from django.urls import reverse

import pytest

from apps.catalog.tests.factories import ProductFactory, ProductVariantFactory
from apps.orders.models import CartItem

pytestmark = pytest.mark.django_db

CART_URL = reverse("cart")
ITEMS_URL = reverse("cart-items")


def add(client, product, variant=None, quantity=1):
    payload = {"product": product.slug, "quantity": quantity}
    if variant is not None:
        payload["variant"] = variant.pk
    return client.post(ITEMS_URL, payload, format="json")


def test_empty_cart(auth_client):
    body = auth_client.get(CART_URL).json()

    assert body == {
        "items": [],
        "count": 0,
        "subtotal": 0,
        "discount": 0,
        "total": 0,
        "requiresGameAccount": False,
    }


def test_add_and_totals(auth_client):
    product = ProductFactory(price=80_000, original_price=100_000)

    body = add(auth_client, product, quantity=2).json()

    assert body["count"] == 2
    assert body["subtotal"] == 200_000
    assert body["discount"] == 40_000
    assert body["total"] == 160_000
    assert body["items"][0]["lineTotal"] == 160_000


def test_adding_same_line_increments(auth_client):
    product = ProductFactory()
    add(auth_client, product)

    body = add(auth_client, product, quantity=2).json()

    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 3


def test_variant_pricing_and_separate_lines(auth_client):
    product = ProductFactory(price=999)
    small = ProductVariantFactory(product=product, price=100)
    big = ProductVariantFactory(product=product, price=300)
    add(auth_client, product, small)

    body = add(auth_client, product, big).json()

    assert [item["unitPrice"] for item in body["items"]] == [100, 300]
    assert body["total"] == 400


def test_variant_required_when_product_has_variants(auth_client):
    product = ProductFactory()
    ProductVariantFactory(product=product)

    response = add(auth_client, product)

    assert response.status_code == 400
    assert response.json()["code"] == "variant_required"


def test_variant_of_another_product_rejected(auth_client):
    product = ProductFactory()
    foreign = ProductVariantFactory()

    assert add(auth_client, product, foreign).json()["code"] == "invalid_variant"


def test_stock_is_enforced(auth_client):
    product = ProductFactory(stock=2)

    response = add(auth_client, product, quantity=3)

    assert response.status_code == 409
    assert response.json()["code"] == "out_of_stock"


def test_inactive_product_rejected(auth_client):
    product = ProductFactory(is_active=False)

    assert add(auth_client, product).json()["code"] == "product_unavailable"


def test_update_quantity_and_zero_removes(auth_client):
    product = ProductFactory()
    item_id = add(auth_client, product).json()["items"][0]["id"]
    url = reverse("cart-item-detail", args=[item_id])

    assert auth_client.patch(url, {"quantity": 5}, format="json").json()["count"] == 5
    assert auth_client.patch(url, {"quantity": 0}, format="json").json()["items"] == []


def test_cannot_touch_someone_elses_line(auth_client, client_for):
    from apps.accounts.tests.factories import UserFactory

    other = client_for(UserFactory())
    item_id = add(other, ProductFactory()).json()["items"][0]["id"]

    response = auth_client.patch(reverse("cart-item-detail", args=[item_id]), {"quantity": 2}, format="json")

    assert response.status_code == 404


def test_remove_and_clear(auth_client):
    first = add(auth_client, ProductFactory()).json()["items"][0]["id"]
    add(auth_client, ProductFactory())

    assert len(auth_client.delete(reverse("cart-item-detail", args=[first])).json()["items"]) == 1
    assert auth_client.delete(CART_URL).json()["items"] == []


def test_merge_guest_cart(auth_client):
    available = ProductFactory(slug="ok")
    sold_out = ProductFactory(slug="gone", stock=0)
    add(auth_client, available)

    response = auth_client.post(
        reverse("cart-merge"),
        {"items": [{"product": "ok", "quantity": 2}, {"product": "gone", "quantity": 1}]},
        format="json",
    )

    body = response.json()
    assert body["cart"]["count"] == 3
    assert body["skipped"] == [{"product": sold_out.slug, "reason": "out_of_stock"}]


def test_requires_game_account_flag(auth_client):
    product = ProductFactory(delivery_type="account")

    assert add(auth_client, product).json()["requiresGameAccount"] is True


def test_cart_requires_auth(api_client):
    assert api_client.get(CART_URL).status_code == 401


def test_quantity_capped_per_line(auth_client):
    product = ProductFactory()
    add(auth_client, product, quantity=20)
    add(auth_client, product, quantity=5)

    assert CartItem.objects.get().quantity == 20
