from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OrdersConfig(AppConfig):
    name = "apps.orders"
    label = "orders"
    verbose_name = _("Orders")

    def ready(self):
        from .services import register_payment_handlers

        register_payment_handlers()
