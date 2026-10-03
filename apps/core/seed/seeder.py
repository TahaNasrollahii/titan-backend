"""Idempotent demo-data seeder.

Reference data is upserted on natural keys. Generated activity (registrations, brackets, orders,
notifications) is only created when absent, and goes through the real services wherever possible
so seeded data obeys the same rules as production data.
"""

import random
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Badge, Friendship, GameAccount, RankTier, User, UserBadge
from apps.accounts.ranks import clear_tier_cache
from apps.accounts.services import heartbeat, send_friend_request
from apps.catalog import services as catalog_services
from apps.catalog.models import Game, Platform, Product, ProductCategory, ProductVariant
from apps.content.models import Announcement, ContactChannel, Promo, SiteSetting
from apps.notifications.services import Kind, notify
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.payments.models import PaymentMethod, WalletTransaction
from apps.payments.services import credit
from apps.teams import services as team_services
from apps.teams.models import Team, TeamMembership
from apps.tournaments.models import (
    Match,
    PlayerStats,
    Registration,
    RegistrationMember,
    Season,
    TeamStats,
    Tournament,
    TournamentPrize,
)
from apps.tournaments.services import bracket, results

from . import data

ADMIN_PHONE = "09000000000"
PLAYER_PHONE_PREFIX = "0991"
FILLER_PHONE_PREFIX = "0992"
FRIEND_PHONE_PREFIX = "0993"
DEMO_SOLO_TOURNAMENT = "titan-apex-solo"


class Seeder:
    def __init__(self, *, assets_dir: Path | None, admin_password: str | None, log=print):
        self.assets_dir = assets_dir if assets_dir and assets_dir.is_dir() else None
        self.admin_password = admin_password
        self.log = log
        self.now = timezone.now()
        self.rng = random.Random(1405)  # deterministic "randomness"
        self.games: dict[str, Game] = {}
        self.teams: dict[str, Team] = {}
        self._filler_index = 0

    # ------------------------------------------------------------------ entry point
    def run(self) -> None:
        with transaction.atomic():
            self.seed_reference_data()
            self.seed_catalog()
            self.seed_users()
            self.seed_teams()
            self.seed_tournaments()
            self.seed_content()
            self.seed_demo_activity()
            self.seed_reviews()
        self.log("Seeding complete.")

    # ------------------------------------------------------------------ helpers
    def _attach(self, instance, field: str, relative_path: str | None) -> None:
        """Copy an image from the frontend's ``public/`` dir into media storage and assign it."""
        if not relative_path or self.assets_dir is None:
            return
        source = self.assets_dir / relative_path
        if not source.is_file():
            return
        upload_to = str(instance._meta.get_field(field).upload_to).rstrip("/") or "seed"
        # Keep the source folder in the name: games/premium.png and categories/premium.png differ.
        name = f"{upload_to}/{source.parent.name}-{source.name}"
        if not default_storage.exists(name):
            with source.open("rb") as handle:
                name = default_storage.save(name, File(handle))
        setattr(instance, field, name)

    def _user(self, username: str, phone: str, **fields) -> User:
        user = User.objects.filter(username=username).first()
        if user is None:
            user = User.objects.create_user(phone=phone, username=username, **fields)
        return user

    def _filler_user(self, prefix: str) -> User:
        self._filler_index += 1
        index = self._filler_index
        username = f"{prefix}_player{index}"
        return self._user(
            username,
            f"{FILLER_PHONE_PREFIX}{index:07d}",
            avatar_seed=self.rng.randint(1, 60),
            level=self.rng.randint(5, 30),
            points=self.rng.randint(200, 4000),
        )

    @property
    def season(self) -> Season:
        return Season.objects.get(is_current=True)

    # ------------------------------------------------------------------ reference data
    def seed_reference_data(self) -> None:
        for tier in data.RANK_TIERS:
            RankTier.objects.update_or_create(slug=tier["slug"], defaults=tier)
        clear_tier_cache()
        for slug, name, icon in data.BADGES:
            Badge.objects.update_or_create(slug=slug, defaults={"name": name, "icon": icon})
        for spec in data.SEASONS:
            Season.objects.update_or_create(
                number=spec["number"],
                defaults={
                    "name": spec["name"],
                    "starts_at": self.now + timedelta(days=spec["start_days"]),
                    "ends_at": self.now + timedelta(days=spec["end_days"]),
                    "is_current": spec["is_current"],
                },
            )
        self.log("Reference data: ranks, badges, seasons.")

    def seed_catalog(self) -> None:
        for order, (slug, name, icon) in enumerate(data.PLATFORMS):
            platform, _ = Platform.objects.update_or_create(
                slug=slug, defaults={"name": name, "sort_order": order}
            )
            self._attach(platform, "icon", icon)
            platform.save()
        for order, (slug, name) in enumerate(data.PRODUCT_CATEGORIES):
            ProductCategory.objects.update_or_create(slug=slug, defaults={"name": name, "sort_order": order})

        for order, spec in enumerate(data.GAMES):
            spec = dict(spec)
            images = spec.pop("images", {})
            game, _ = Game.objects.update_or_create(
                slug=spec.pop("slug"), defaults={**spec, "sort_order": order}
            )
            for field, path in images.items():
                self._attach(game, field, path)
            game.save()
            self.games[game.slug] = game

        platforms = {platform.slug: platform for platform in Platform.objects.all()}
        categories = {category.slug: category for category in ProductCategory.objects.all()}
        for spec in data.PRODUCTS:
            self._product(spec, platforms, categories)
        self.log(f"Catalog: {len(self.games)} games, {len(data.PRODUCTS)} products.")

    def _product(self, spec: dict, platforms: dict, categories: dict) -> Product:
        spec = dict(spec)
        variants = spec.pop("variants", [])
        platform_slugs = spec.pop("platforms", [])
        image = spec.pop("image", None)
        game_slug = spec.pop("game", None)
        product, _ = Product.objects.update_or_create(
            slug=spec.pop("slug"),
            defaults={
                **spec,
                "game": self.games.get(game_slug) if game_slug else None,
                "category": categories[spec.pop("category")],
            },
        )
        self._attach(product, "image", image)
        product.save()
        product.platforms.set([platforms[slug] for slug in platform_slugs])
        for order, (label, price, original, *stock) in enumerate(variants):
            ProductVariant.objects.update_or_create(
                product=product,
                label=label,
                defaults={
                    "price": price,
                    "original_price": original,
                    "stock": stock[0] if stock else None,
                    "sort_order": order,
                },
            )
        return product

    # ------------------------------------------------------------------ users
    def seed_users(self) -> None:
        season = self.season
        badges = {badge.slug: badge for badge in Badge.objects.all()}
        for index, (username, level, xp, game, wins, matches, points, earnings, badge_slugs) in enumerate(
            data.PLAYERS, start=1
        ):
            user = self._user(username, f"{PLAYER_PHONE_PREFIX}{index:07d}")
            User.objects.filter(pk=user.pk).update(
                level=level,
                xp=min(xp, 500 + level * 300 - 1),
                points=points,
                favorite_game=self.games[game],
                avatar_seed=(index % 10) + 1,
                date_joined=self.now - timedelta(days=400 - index * 12),
            )
            PlayerStats.objects.update_or_create(
                user=user,
                game=self.games[game],
                season=season,
                defaults={
                    "matches": matches,
                    "wins": wins,
                    "losses": matches - wins,
                    "points": points,
                    "earnings_usd": earnings,
                },
            )
            for slug in badge_slugs:
                UserBadge.objects.get_or_create(user=user, badge=badges[slug])

        demo = data.DEMO_USER
        user = User.objects.filter(phone=demo["phone"]).first() or User.objects.create_user(
            phone=demo["phone"]
        )
        User.objects.filter(pk=user.pk).update(
            username=demo["username"],
            full_name=demo["full_name"],
            email=demo["email"],
            avatar_seed=demo["avatar_seed"],
            favorite_game=self.games[demo["favorite_game"]],
        )
        self.demo = User.objects.get(pk=user.pk)

        if self.admin_password:
            admin = User.objects.filter(phone=ADMIN_PHONE).first()
            if admin is None:
                User.objects.create_superuser(ADMIN_PHONE, self.admin_password, username="admin")
                self.log(f"Admin: phone {ADMIN_PHONE}.")
        self.log(f"Users: {User.objects.count()} total (demo: {demo['phone']}).")

    # ------------------------------------------------------------------ teams
    def seed_teams(self) -> None:
        season = self.season
        captains = {"Iran Titans": self.demo}
        for name, tag, game_slug, region, points, wins, losses in data.TEAMS:
            team = Team.objects.active().filter(name=name).first()
            if team is None:
                captain = captains.get(name) or self._filler_user(tag.lower())
                team = team_services.create_team(
                    captain, name=name, tag=tag, game=self.games[game_slug], region=region
                )
            Team.objects.filter(pk=team.pk).update(
                points=points, wins=wins, losses=losses, matches_played=wins + losses
            )
            TeamStats.objects.update_or_create(
                team=team,
                game=team.game,
                season=season,
                defaults={"matches": wins + losses, "wins": wins, "losses": losses, "points": points},
            )
            self.teams[name] = team

        iran_titans = self.teams["Iran Titans"]
        for username in data.IRAN_TITANS_ROSTER:
            member = User.objects.get(username=username)
            if not iran_titans.memberships.filter(user=member).exists():
                team_services.join_with_code(member, iran_titans.invite_code)

        sniper_elite = self.teams["Sniper Elite"]
        if not sniper_elite.memberships.filter(user=self.demo).exists():
            team_services.join_with_code(self.demo, sniper_elite.invite_code)

        for team in self.teams.values():
            self._fill_roster(team, size=5)
        self.log(f"Teams: {len(self.teams)}.")

    def _fill_roster(self, team: Team, size: int) -> None:
        missing = size - team.memberships.count()
        for _ in range(max(0, missing)):
            team_services.join_with_code(self._filler_user(team.tag.lower()), team.invite_code)

    def _generic_teams(self, game: Game, count: int, team_size: int) -> list[Team]:
        teams = list(Team.objects.active().filter(game=game).order_by("-points")[:count])
        while len(teams) < count:
            number = len(teams) + 1
            tag = f"{game.title_en[:2].upper()}{number}"
            name = f"{game.title_en} Squad {number}"
            team = Team.objects.active().filter(name=name).first() or team_services.create_team(
                self._filler_user(tag.lower()), name=name, tag=tag, game=game, region=Team.Region.IRAN
            )
            teams.append(team)
        for team in teams:
            self._fill_roster(team, size=team_size)
        return teams

    # ------------------------------------------------------------------ tournaments
    def seed_tournaments(self) -> None:
        for spec in data.TOURNAMENTS:
            tournament = self._tournament(spec)
            if not tournament.registrations.exists():
                self._register_entrants(tournament, spec)
                self._play(tournament, spec)
        self.log(f"Tournaments: {len(data.TOURNAMENTS)}.")

    def _tournament(self, spec: dict) -> Tournament:
        start = self.now + timedelta(days=spec["start"])
        opens, closes = spec.get("reg", (spec["start"] - 14, spec["start"] - 0.1))
        tournament, _ = Tournament.objects.update_or_create(
            slug=spec["slug"],
            defaults={
                "title": spec["title"],
                "game": self.games[spec["game"]],
                "season": self.season,
                "description": spec.get("description", ""),
                "participant_type": spec["type"],
                "team_size": spec.get("team_size", 1),
                "format_label": spec.get("format_label", ""),
                "best_of": spec.get("best_of", 1),
                "region": spec.get("region", "me"),
                "max_participants": spec["max"],
                "entry_fee": spec.get("entry_fee", 0),
                "entry_fee_original": spec.get("entry_fee_original"),
                "prize_pool": spec["prize_pool"],
                "prize_currency": spec["currency"],
                "rules": spec.get("rules", []),
                "registration_opens_at": self.now + timedelta(days=opens),
                "registration_closes_at": self.now + timedelta(days=closes),
                "starts_at": start,
                "ends_at": start + timedelta(days=spec["days"]),
                "is_featured": spec.get("featured", False),
                "viewer_count": spec.get("viewers", 0),
            },
        )
        game = tournament.game
        if not tournament.cover_image and game.background_image:
            tournament.cover_image = game.background_image.name
            tournament.save(update_fields=["cover_image"])
        for place, label, amount, points in spec.get("prizes", []):
            TournamentPrize.objects.update_or_create(
                tournament=tournament,
                place=place,
                defaults={"label": label, "amount": amount, "points": points},
            )
        return tournament

    def _register_entrants(self, tournament: Tournament, spec: dict) -> None:
        count = spec.get("entrants", 0)
        if not count:
            return
        if tournament.is_team:
            named = [self.teams[name] for name in spec.get("teams", [])]
            entrants = named + self._generic_teams(tournament.game, count - len(named), tournament.team_size)
            entrants = entrants[:count]
            for team in entrants:
                self._fill_roster(team, size=tournament.team_size)
                roster = [m.user for m in team.memberships.select_related("user")]
                captain = next(
                    m.user for m in team.memberships.all() if m.role == TeamMembership.Role.CAPTAIN
                )
                self._confirmed_registration(tournament, roster, team=team, registered_by=captain)
            return

        players = list(
            User.objects.filter(is_staff=False, username__isnull=False)
            .exclude(pk=self.demo.pk)
            .order_by("-points")
        )
        players.sort(key=lambda user: user.favorite_game_id != tournament.game_id)  # fans of the game first
        if tournament.slug == DEMO_SOLO_TOURNAMENT:  # the demo user is registered here (dashboard mock)
            entrants = [*players[: count - 1], self.demo]
        else:
            entrants = players[:count]
        for player in entrants:
            self._confirmed_registration(tournament, [player], player=player, registered_by=player)

    def _confirmed_registration(self, tournament, roster, *, registered_by, team=None, player=None):
        registration = Registration.objects.create(
            tournament=tournament,
            team=team,
            player=player,
            registered_by=registered_by,
            status=Registration.Status.CONFIRMED,
            entry_fee_paid=tournament.entry_fee,
            confirmed_at=tournament.registration_opens_at + timedelta(hours=self.rng.randint(1, 48)),
        )
        RegistrationMember.objects.bulk_create(
            RegistrationMember(registration=registration, user=u) for u in roster
        )
        return registration

    def _random_scores(self) -> tuple[int, int]:
        loser_score = self.rng.randint(4, 11)
        return (13, loser_score) if self.rng.random() < 0.7 else (loser_score, 13)

    def _play(self, tournament: Tournament, spec: dict) -> None:
        mode = spec.get("results")
        if not mode:
            return
        planned_end = tournament.ends_at
        bracket.generate_bracket(tournament)

        if mode == "mock_bracket":
            self._play_mock_bracket(tournament)
            return
        rounds = results.total_rounds(tournament)
        last_round = rounds if mode == "all" else (1 if rounds > 1 else 0)
        for round_number in range(1, last_round + 1):
            for match in tournament.matches.filter(round=round_number, status=Match.Status.SCHEDULED):
                score_a, score_b = self._random_scores()
                results.report_result(match, score_a=score_a, score_b=score_b)
        if mode == "all":  # keep the historic end date instead of "now"
            Tournament.objects.filter(pk=tournament.pk).update(ends_at=planned_end)

    def _play_mock_bracket(self, tournament: Tournament) -> None:
        scores = data.MOCK_BRACKET_SCORES
        for match, (score_a, score_b) in zip(
            tournament.matches.filter(round=1).order_by("position"), scores["round_1"], strict=True
        ):
            results.report_result(match, score_a=score_a, score_b=score_b)
        semis = {match.position: match for match in tournament.matches.filter(round=2)}
        for position, (score_a, score_b) in scores["round_2_completed"].items():
            results.report_result(semis[position], score_a=score_a, score_b=score_b)
        results.update_match(
            semis[scores["round_2_live"]], status=Match.Status.LIVE, lobby_code="TITAN-SF1-7731"
        )

    # ------------------------------------------------------------------ content
    def seed_content(self) -> None:
        for order, spec in enumerate(data.PROMOS):
            spec = dict(spec)
            image, background = spec.pop("image", None), spec.pop("background_image", None)
            product, tournament = spec.pop("product", None), spec.pop("tournament", None)
            ends_in = spec.pop("ends_in_seconds", None)
            promo, _ = Promo.objects.update_or_create(
                placement=spec.pop("placement"),
                title=spec.pop("title"),
                defaults={
                    **spec,
                    "sort_order": order,
                    "product": Product.objects.filter(slug=product).first() if product else None,
                    "tournament": Tournament.objects.filter(slug=tournament).first() if tournament else None,
                    "ends_at": self.now + timedelta(seconds=ends_in) if ends_in else None,
                },
            )
            self._attach(promo, "image", image)
            self._attach(promo, "background_image", background)
            promo.save()

        for order, (title, text, icon, link) in enumerate(data.ANNOUNCEMENTS):
            Announcement.objects.update_or_create(
                title=title, defaults={"text": text, "icon": icon, "link": link, "sort_order": order}
            )
        for order, spec in enumerate(data.CONTACT_CHANNELS):
            spec = dict(spec)
            icon = spec.pop("icon", None)
            channel, _ = ContactChannel.objects.update_or_create(
                kind=spec.pop("kind"), defaults={**spec, "sort_order": order}
            )
            self._attach(channel, "icon", icon)
            channel.save()
        SiteSetting.objects.update_or_create(
            key="support_online", defaults={"value": True, "description": "Show the support team as online."}
        )
        self.log("Content: promos, announcements, contact channels.")

    # ------------------------------------------------------------------ demo user activity
    def seed_demo_activity(self) -> None:
        demo, spec = self.demo, data.DEMO_USER
        accounts = {}
        for title, username, game in spec["game_accounts"]:
            account, _ = GameAccount.objects.get_or_create(
                user=demo,
                title=title,
                defaults={"username": username, "password": "demo-password", "game": self.games[game]},
            )
            accounts[game] = account

        if not Order.objects.filter(user=demo).exists():
            self._demo_orders(accounts["fortnite"], spec["wallet_balance"])

        for index, (name, presence, game) in enumerate(spec["friends"], start=1):
            friend = self._user(name, f"{FRIEND_PHONE_PREFIX}{index:07d}", avatar_seed=index * 5)
            Friendship.objects.get_or_create(
                from_user=demo, to_user=friend, defaults={"status": Friendship.Status.ACCEPTED}
            )
            heartbeat(friend, status=presence, game=self.games.get(game) if game else None)

        night_wolf = User.objects.get(username="NightWolf")
        if not Friendship.objects.filter(from_user=night_wolf, to_user=demo).exists():
            send_friend_request(night_wolf, demo)

        shadow_strike = self.teams["Shadow Strike"]
        if not shadow_strike.invitations.filter(invited_user=demo).exists():
            captain = shadow_strike.memberships.get(role=TeamMembership.Role.CAPTAIN).user
            team_services.invite(shadow_strike, captain, demo)

        apex_solo = Tournament.objects.get(slug=DEMO_SOLO_TOURNAMENT)
        if not demo.notifications.filter(kind=Kind.TOURNAMENT).exists():
            notify(
                demo,
                kind=Kind.TOURNAMENT,
                title="تایید ثبت‌نام در تورنومنت",
                body=f"ثبت‌نام شما در {apex_solo.title} با موفقیت تایید شد.",
                icon="trophy",
                data={"tournament_slug": apex_solo.slug},
            )
        self.log("Demo activity: orders, game accounts, friends, invitations, notifications.")

    def _demo_orders(self, game_account: GameAccount, final_balance: int) -> None:
        vbucks = Product.objects.get(slug="fortnite-vbucks")
        vbucks_2800 = vbucks.variants.get(price=749000)
        mouse = Product.objects.get(slug="titan-m40-mouse")
        credit(
            self.demo,
            final_balance + vbucks_2800.price + mouse.price,
            kind=WalletTransaction.Kind.TOPUP,
            description="شارژ کیف پول",
        )
        order_services.add_to_cart(self.demo, vbucks, vbucks_2800)
        delivered = order_services.checkout(
            self.demo, payment_method=PaymentMethod.WALLET, game_account=game_account
        ).order
        order_services.complete_order(delivered)

        order_services.add_to_cart(self.demo, mouse)
        processing = order_services.checkout(self.demo, payment_method=PaymentMethod.WALLET).order
        Order.objects.filter(pk=processing.pk).update(status=Order.Status.PROCESSING)
        Order.objects.filter(pk=delivered.pk).update(created_at=self.now - timedelta(days=2))
        Order.objects.filter(pk=processing.pk).update(created_at=self.now - timedelta(days=5))

    # ------------------------------------------------------------------ reviews
    def seed_reviews(self) -> None:
        reviewers = list(User.objects.filter(username__in=[row[0] for row in data.PLAYERS[:12]]))
        for product in Product.objects.filter(reviews__isnull=True):
            for reviewer in self.rng.sample(reviewers, k=3):
                catalog_services.submit_review(
                    reviewer,
                    product,
                    author_name=reviewer.username,
                    author_email=reviewer.email or f"{reviewer.username.lower()}@example.com",
                    rating=self.rng.choice([5, 5, 5, 4, 4, 3]),
                    comment=self.rng.choice(data.REVIEW_COMMENTS),
                )
        self.log("Reviews seeded.")


def default_assets_dir() -> Path:
    return settings.BASE_DIR.parent / "titan-front" / "public"
