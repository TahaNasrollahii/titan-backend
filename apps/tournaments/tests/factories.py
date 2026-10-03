from datetime import timedelta

from django.utils import timezone

import factory

from apps.catalog.tests.factories import GameFactory
from apps.tournaments.models import Registration, RegistrationMember, Season, Tournament


class SeasonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Season
        django_get_or_create = ("number",)

    number = 3
    name = "فصل ۳"
    starts_at = factory.LazyFunction(lambda: timezone.now() - timedelta(days=30))
    ends_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=60))
    is_current = True


class TournamentFactory(factory.django.DjangoModelFactory):
    """Defaults to a free solo tournament whose registration is currently open."""

    class Meta:
        model = Tournament

    slug = factory.Sequence(lambda n: f"tournament-{n}")
    title = factory.Sequence(lambda n: f"Tournament {n}")
    game = factory.SubFactory(GameFactory, slug="valorant", title_en="Valorant")
    season = factory.SubFactory(SeasonFactory)
    participant_type = Tournament.ParticipantType.SOLO
    team_size = 1
    max_participants = 16
    registration_opens_at = factory.LazyFunction(lambda: timezone.now() - timedelta(days=1))
    registration_closes_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=1))
    starts_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=2))
    ends_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=3))

    class Params:
        team = factory.Trait(participant_type=Tournament.ParticipantType.TEAM, team_size=2)
        upcoming = factory.Trait(
            registration_opens_at=factory.LazyFunction(lambda: timezone.now() + timedelta(days=1)),
            registration_closes_at=factory.LazyFunction(lambda: timezone.now() + timedelta(days=2)),
        )


def confirm_player(tournament: Tournament, user) -> Registration:
    registration = Registration.objects.create(
        tournament=tournament,
        player=user,
        registered_by=user,
        status=Registration.Status.CONFIRMED,
        confirmed_at=timezone.now(),
    )
    RegistrationMember.objects.create(registration=registration, user=user)
    return registration


def confirm_team(tournament: Tournament, team) -> Registration:
    captain = team.memberships.get(role="captain").user
    registration = Registration.objects.create(
        tournament=tournament,
        team=team,
        registered_by=captain,
        status=Registration.Status.CONFIRMED,
        confirmed_at=timezone.now(),
    )
    for membership in team.memberships.all():
        RegistrationMember.objects.create(registration=registration, user=membership.user)
    return registration
