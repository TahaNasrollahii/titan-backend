from django.conf import settings
from django.utils import translation


class AdminEnglishMiddleware:
    """Serve the staff admin with bilingual support (EN/FA) and API with Persian default.

    Must run after ``LocaleMiddleware`` so it manages admin/API language scopes.
    """

    ADMIN_PREFIX = "/admin/"
    LANGUAGE = "en"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(self.ADMIN_PREFIX):
            lang = translation.get_language_from_request(request) or "fa"
            translation.activate(lang)
            request.LANGUAGE_CODE = lang
            response = self.get_response(request)
            response.headers.setdefault("Content-Language", lang)
            return response

        # Admin panel: check cookie then session for explicitly chosen language
        cookie_name = getattr(settings, "LANGUAGE_COOKIE_NAME", "django_language")
        cookie_lang = request.COOKIES.get(cookie_name)
        session_lang = request.session.get(cookie_name) if hasattr(request, "session") else None
        user_lang = cookie_lang or session_lang

        if user_lang in ("fa", "en"):
            admin_lang = user_lang
        else:
            admin_lang = self.LANGUAGE

        translation.activate(admin_lang)
        request.LANGUAGE_CODE = admin_lang
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", admin_lang)
        return response

