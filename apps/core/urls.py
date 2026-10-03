from django.urls import path

from . import aggregates

urlpatterns = [
    path("home/", aggregates.HomeView.as_view(), name="home"),
    path("search/", aggregates.SearchView.as_view(), name="search"),
    path("me/dashboard/", aggregates.DashboardView.as_view(), name="dashboard"),
]
