from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ContentConfig(AppConfig):
    name = "apps.content"
    label = "content"
    verbose_name = _("Site content")
