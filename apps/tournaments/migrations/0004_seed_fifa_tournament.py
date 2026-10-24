"""Create the FIFA showcase tournament once.

This used to run in TournamentsConfig.ready(), which put a table introspection and
three queries in front of every process start (every serverless cold start on Vercel).
"""

from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def seed_fifa_tournament(apps, schema_editor):
    Game = apps.get_model("catalog", "Game")
    Season = apps.get_model("tournaments", "Season")
    Tournament = apps.get_model("tournaments", "Tournament")

    if Tournament.objects.filter(slug="fifa").exists():
        return

    now = timezone.now()
    game = Game.objects.filter(slug__in=["fifa", "ea-fc"]).first()
    if game is None:
        game = Game.objects.create(slug="fifa", title="فیفا ۲۴", title_en="FIFA 24", genre="ورزشی", kind="game")

    season = Season.objects.order_by("number").first()
    if season is None:
        season = Season.objects.create(
            number=1,
            name="فصل اول تایتان",
            starts_at=now - timedelta(days=30),
            ends_at=now + timedelta(days=90),
        )

    Tournament.objects.create(
        slug="fifa",
        title="مسابقات فیفا",
        game=game,
        season=season,
        description="تورنمنت حذفی جام ویژه فیفا ۲۴ با حضور ۱۶ تیم برتر و جدول مسابقات زنده.",
        team_size=1,
        format_label="تیمی حذفی — ۱۶ تیم",
        max_participants=16,
        entry_fee=0,
        prize_pool=150000000,
        prize_currency="IRT",
        rules=[
            "مسابقات به صورت تک‌حذفی (Knockout) برگزار می‌شود",
            "زمان هر بازی: دو نیمه ۶ دقیقه‌ای",
            "استفاده از تمام تیم‌های رسمی مجاز است",
            "در صورت تساوی، وقت اضافه و پنالتی تعیین‌کننده است",
        ],
        registration_opens_at=now - timedelta(days=7),
        registration_closes_at=now - timedelta(days=1),
        starts_at=now - timedelta(hours=12),
        ends_at=now + timedelta(days=4),
        state="live",
        is_featured=True,
        viewer_count=12408,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_alter_game_options_alter_platform_options_and_more"),
        ("tournaments", "0003_alter_match_options_alter_playerstats_options_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_fifa_tournament, migrations.RunPython.noop),
    ]
