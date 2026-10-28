from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from django.http import HttpResponse

from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.core.admin_auth import TitanAdminAuthenticationForm
from apps.core.views import HealthCheckView

admin.site.login_form = TitanAdminAuthenticationForm


def robots_txt(request):
    admin_slug = getattr(settings, "ADMIN_URL", "manage-titan").strip("/")
    admin_path = f"/{admin_slug}/"
    lines = [
        "User-agent: *",
        f"Disallow: {admin_path}",
        "Disallow: /admin/",
        "Disallow: /api/docs/",
        "Disallow: /api/redoc/",
        "Disallow: /api/schema/",
        "Disallow: /api/health/",
        "Disallow: /api/v1/",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def security_txt(request):
    contact_email = getattr(settings, "SECURITY_CONTACT_EMAIL", "tahanasrollahii@gmail.com")
    lines = [
        f"Contact: mailto:{contact_email}",
        "Expires: 2027-12-31T23:59:59.000Z",
        "Preferred-Languages: fa, en",
        "Canonical: https://titan-backend-taha.vercel.app/.well-known/security.txt",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")

api_v1 = [
    path("", include("apps.accounts.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.payments.urls")),
    path("", include("apps.notifications.urls")),
    path("", include("apps.teams.urls")),
    path("", include("apps.tournaments.urls")),
    path("", include("apps.content.urls")),
    path("", include("apps.support.urls")),
    path("", include("apps.telegram.urls")),
    path("", include("apps.core.urls")),
]

admin_slug = getattr(settings, "ADMIN_URL", "manage-titan").strip("/")
admin_route = f"{admin_slug}/" if admin_slug else "admin/"

urlpatterns = [
    path("robots.txt", robots_txt, name="robots_txt"),
    path(".well-known/security.txt", security_txt, name="security_txt"),
    path("i18n/", include("django.conf.urls.i18n")),
    path(admin_route, admin.site.urls),
    path("api/health/", HealthCheckView.as_view(), name="health"),
    path("api/v1/", include(api_v1)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "Titan"
admin.site.site_title = "Titan admin"
