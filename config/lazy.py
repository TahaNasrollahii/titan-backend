"""Lazy strings for settings that can be stringified before Django is set up.

Vercel's build reads every setting with ``json.dumps(..., default=str)`` before the app registry is
loaded, where Django's own ``gettext_lazy``/``reverse_lazy`` raise AppRegistryNotReady. Once apps are
ready, these behave exactly like Django's.
"""

from django.apps import apps
from django.urls import reverse
from django.utils.functional import lazy
from django.utils.translation import gettext


def _gettext(message: str) -> str:
    return gettext(message) if apps.ready else message


def _reverse(viewname: str, *args, **kwargs) -> str:
    return reverse(viewname, *args, **kwargs) if apps.ready else ""


gettext_lazy = lazy(_gettext, str)
reverse_lazy = lazy(_reverse, str)
