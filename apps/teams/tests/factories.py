import factory

from apps.accounts.tests.factories import UserFactory
from apps.catalog.tests.factories import GameFactory
from apps.teams.models import Team, TeamMembership


class TeamFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Team

    name = factory.Sequence(lambda n: f"Team {n}")
    tag = factory.Sequence(lambda n: f"T{n % 1000}")
    game = factory.SubFactory(GameFactory, slug="valorant", title_en="Valorant")


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TeamMembership

    team = factory.SubFactory(TeamFactory)
    user = factory.SubFactory(UserFactory)
    role = TeamMembership.Role.PLAYER


def team_with_members(size: int, **team_kwargs) -> Team:
    """A team whose first member is the captain, with ``size`` members in total."""
    team = TeamFactory(**team_kwargs)
    MembershipFactory(team=team, role=TeamMembership.Role.CAPTAIN)
    MembershipFactory.create_batch(size - 1, team=team)
    return team


def captain_of(team: Team):
    return team.memberships.get(role=TeamMembership.Role.CAPTAIN).user
