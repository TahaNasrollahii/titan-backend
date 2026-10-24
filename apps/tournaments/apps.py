from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TournamentsConfig(AppConfig):
    name = "apps.tournaments"
    label = "tournaments"
    verbose_name = _("Tournaments")

    def ready(self):
        from .services.registration import register_payment_handlers

        register_payment_handlers()
