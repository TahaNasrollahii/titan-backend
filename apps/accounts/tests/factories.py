import factory

from apps.accounts.models import Badge, GameAccount, RankTier, User


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    phone = factory.Sequence(lambda n: f"0912{n:07d}")
    username = factory.Sequence(lambda n: f"player{n}")
    full_name = factory.Faker("name")


class GameAccountFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GameAccount

    user = factory.SubFactory(UserFactory)
    title = "Main account"
    username = factory.Sequence(lambda n: f"gamer{n}@example.com")
    password = "s3cret-pass"


class RankTierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RankTier
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"tier-{n}")
    name = factory.Sequence(lambda n: f"Tier {n}")
    min_points = factory.Sequence(lambda n: n * 1000)
    color_from = "#fff"
    color_to = "#000"
    glow = "rgba(0,0,0,.5)"
    ornament = "star"


class BadgeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Badge
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"badge-{n}")
    name = factory.Sequence(lambda n: f"Badge {n}")
