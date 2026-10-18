from django.conf import settings
from django.utils import translation


class AdminEnglishMiddleware:
    """Serve the staff admin with bilingual support (EN/FA) and API with Persian default.

    Must run after ``LocaleMiddleware`` so it manages admin/API language scopes.
    """

    LANGUAGE = "en"

    def __init__(self, get_response):
        self.get_response = get_response

    @property
    def admin_prefix(self):
        admin_slug = getattr(settings, "ADMIN_URL", "manage-titan").strip("/")
        return f"/{admin_slug}/"

    def __call__(self, request):
        if not (request.path.startswith(self.admin_prefix) or request.path.startswith("/admin/")):
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


class SecurityHeadersMiddleware:
    """Enforce defensive security headers (CSP, Permissions-Policy, X-Robots-Tag) on backend responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        admin_slug = getattr(settings, "ADMIN_URL", "manage-titan").strip("/")
        admin_prefix = f"/{admin_slug}/"
        is_admin_or_docs = (
            request.path.startswith(admin_prefix)
            or request.path.startswith("/admin/")
            or request.path.startswith("/api/docs/")
            or request.path.startswith("/api/redoc/")
            or request.path.startswith("/api/schema/")
        )

        if "Content-Security-Policy" not in response.headers:
            # Django Unfold admin relies on Alpine.js which requires 'unsafe-eval' for expression evaluation.
            script_src = "'self' 'unsafe-inline' 'unsafe-eval'" if is_admin_or_docs else "'self' 'unsafe-inline'"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "img-src 'self' data: https: blob:; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' data: https://fonts.gstatic.com; "
                f"script-src {script_src}; "
                "frame-ancestors 'none'; "
                "object-src 'none'; "
                "base-uri 'self';"
            )
        if "Permissions-Policy" not in response.headers:
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), browsing-topics=()"

        if is_admin_or_docs:
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"

        return response


PUBLIC_CACHE_PREFIXES = (
    "/api/v1/products/",
    "/api/v1/games/",
    "/api/v1/tournaments/",
    "/api/v1/home/",
    "/api/v1/content/",
    "/api/v1/categories/",
)


class PublicApiCacheMiddleware:
    """Set Edge caching headers on public read-only API endpoints for anonymous requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            request.method in ("GET", "HEAD")
            and response.status_code == 200
            and any(request.path.startswith(prefix) for prefix in PUBLIC_CACHE_PREFIXES)
        ):
            has_auth = (
                request.headers.get("Authorization")
                or (hasattr(request, "user") and request.user.is_authenticated)
            )
            if not has_auth and "Cache-Control" not in response.headers:
                response.headers["Cache-Control"] = "public, s-maxage=300, stale-while-revalidate=600"
        return response
