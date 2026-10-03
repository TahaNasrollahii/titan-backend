import factory

from apps.catalog.models import Game, Platform, Product, ProductCategory, ProductVariant


class GameFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Game
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"game-{n}")
    title = factory.Sequence(lambda n: f"بازی {n}")
    title_en = factory.Sequence(lambda n: f"Game {n}")


class PlatformFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Platform
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"platform-{n}")
    name = factory.Sequence(lambda n: f"Platform {n}")


class ProductCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductCategory
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"category-{n}")
    name = factory.Sequence(lambda n: f"Category {n}")


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Product

    slug = factory.Sequence(lambda n: f"product-{n}")
    title = factory.Sequence(lambda n: f"Product {n}")
    category = factory.SubFactory(ProductCategoryFactory)
    price = 100_000


class ProductVariantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProductVariant

    product = factory.SubFactory(ProductFactory)
    label = factory.Sequence(lambda n: f"Option {n}")
    price = 50_000
