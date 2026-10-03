from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.core.seed.seeder import Seeder, default_assets_dir


class Command(BaseCommand):
    help = "Load demo data mirroring the Titan frontend mocks. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Delete ALL data before seeding.")
        parser.add_argument(
            "--assets-dir",
            type=Path,
            default=None,
            help="Frontend public/ directory to copy images from (default: ../titan-front/public).",
        )
        parser.add_argument(
            "--admin-password",
            default=None,
            help="Superuser password (default: $SEED_ADMIN_PASSWORD, or 'admin' when DEBUG).",
        )

    def handle(self, *args, **options):
        if options["flush"]:
            if not settings.DEBUG:
                raise CommandError("--flush is only allowed with DEBUG=True.")
            call_command("flush", interactive=False, verbosity=0)
            self.stdout.write("Database flushed.")

        admin_password = options["admin_password"] or settings.SEED_ADMIN_PASSWORD
        if not admin_password and settings.DEBUG:
            admin_password = "admin"
        if not admin_password:
            self.stdout.write(self.style.WARNING("No admin password given; skipping superuser creation."))

        assets_dir = options["assets_dir"] or default_assets_dir()
        if not assets_dir.is_dir():
            self.stdout.write(self.style.WARNING(f"Assets dir {assets_dir} not found; images are skipped."))

        Seeder(assets_dir=assets_dir, admin_password=admin_password, log=self.stdout.write).run()
        self.stdout.write(self.style.SUCCESS("Done."))
