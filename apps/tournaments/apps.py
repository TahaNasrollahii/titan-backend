from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TournamentsConfig(AppConfig):
    name = "apps.tournaments"
    label = "tournaments"
    verbose_name = _("Tournaments")

    def ready(self):
        from .services.registration import register_payment_handlers

        register_payment_handlers()

        try:
            self._ensure_fifa_tournament()
        except Exception:
            pass

    def _ensure_fifa_tournament(self):
        from django.db import connection

        table_names = connection.introspection.table_names()
        if "tournaments_tournament" not in table_names or "catalog_game" not in table_names:
            return

        from datetime import timedelta
        from django.utils import timezone
        from apps.catalog.models import Game
        from .models import Season, Tournament

        game = Game.objects.filter(slug__in=["fifa", "ea-fc"]).first()
        if not game:
            game = Game.objects.create(
                slug="fifa",
                title="فیفا ۲۴",
                title_en="FIFA 24",
                genre="ورزشی",
                kind="game",
            )

        season = Season.objects.first()
        if not season:
            now = timezone.now()
            season = Season.objects.create(
                slug="season-1",
                name="فصل اول تایتان",
                starts_at=now - timedelta(days=30),
                ends_at=now + timedelta(days=90),
            )

        now = timezone.now()
        Tournament.objects.get_or_create(
            slug="fifa",
            defaults={
                "title": "مسابقات فیفا",
                "game": game,
                "season": season,
                "description": "تورنمنت حذفی جام ویژه فیفا ۲۴ با حضور ۱۶ تیم برتر و جدول مسابقات زنده.",
                "team_size": 1,
                "format_label": "تیمی حذفی — ۱۶ تیم",
                "max_participants": 16,
                "entry_fee": 0,
                "prize_pool": 150000000,
                "prize_currency": "IRT",
                "rules": [
                    "مسابقات به صورت تک‌حذفی (Knockout) برگزار می‌شود",
                    "زمان هر بازی: دو نیمه ۶ دقیقه‌ای",
                    "استفاده از تمام تیم‌های رسمی مجاز است",
                    "در صورت تساوی، وقت اضافه و پنالتی تعیین‌کننده است",
                ],
                "registration_opens_at": now - timedelta(days=7),
                "registration_closes_at": now - timedelta(days=1),
                "starts_at": now - timedelta(hours=12),
                "ends_at": now + timedelta(days=4),
                "state": Tournament.State.LIVE,
                "is_featured": True,
                "viewer_count": 12408,
            },
        )
