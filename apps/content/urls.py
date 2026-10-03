from django.urls import path

from . import views

urlpatterns = [
    path("content/promos/", views.PromoListView.as_view(), name="promo-list"),
    path("content/announcements/", views.AnnouncementListView.as_view(), name="announcement-list"),
    path("content/contact/", views.ContactInfoView.as_view(), name="contact-info"),
]
