from django.utils import translation


class AdminEnglishMiddleware:
    """Serve the staff admin in English while the public API stays Persian (``LANGUAGE_CODE = "fa"``).

    Must run after ``LocaleMiddleware`` so it overrides the language that middleware activated.
    """

    ADMIN_PREFIX = "/admin/"
    LANGUAGE = "en"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(self.ADMIN_PREFIX):
            return self.get_response(request)
        translation.activate(self.LANGUAGE)
        request.LANGUAGE_CODE = self.LANGUAGE
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", self.LANGUAGE)
        return response
