import logging
import sys
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class SupportConfig(AppConfig):
    name = "apps.support"
    label = "support"
    verbose_name = _("Live support")

    def ready(self):
        if any(arg in sys.argv for arg in ("runserver", "rs")):
            try:
                from django.core.management import call_command
                call_command("migrate", "support", interactive=False)
            except Exception as e:
                logger.warning("Auto-migration for support app failed: %s", e)

