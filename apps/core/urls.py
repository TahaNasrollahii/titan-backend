from django.urls import path

from . import aggregates

urlpatterns = [
    path("home/", aggregates.HomeView.as_view(), name="home"),
    path("search/", aggregates.SearchView.as_view(), name="search"),
    path("stats/", aggregates.PlatformStatsView.as_view(), name="platform-stats"),
    path("me/dashboard/", aggregates.DashboardView.as_view(), name="dashboard"),
    path("seo/sitemap/", aggregates.SitemapDataView.as_view(), name="seo-sitemap"),
    path("seo/redirects/", aggregates.RedirectListView.as_view(), name="seo-redirects"),
]
