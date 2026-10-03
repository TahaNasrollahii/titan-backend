from django.urls import reverse

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.notifications.services import notify, notify_many

pytestmark = pytest.mark.django_db

LIST_URL = reverse("notification-list")


def test_list_is_private_and_newest_first(auth_client, user):
    notify(user, title="first")
    notify(user, title="second", data={"invitation_id": 7})
    notify(UserFactory(), title="not mine")

    results = auth_client.get(LIST_URL).json()["results"]

    assert [n["title"] for n in results] == ["second", "first"]
    assert results[0]["data"] == {"invitationId": 7}


def test_unread_filter_and_count(auth_client, user):
    notify(user, title="a")
    read = notify(user, title="b")
    read.is_read = True
    read.save()

    assert auth_client.get(reverse("notification-unread-count")).json() == {"count": 1}
    assert [n["title"] for n in auth_client.get(LIST_URL, {"unread": "1"}).json()["results"]] == ["a"]


def test_mark_read(auth_client, user):
    notification = notify(user, title="a")

    body = auth_client.post(reverse("notification-read", args=[notification.pk])).json()

    assert body["isRead"] is True


def test_cannot_mark_others(auth_client):
    foreign = notify(UserFactory(), title="x")

    assert auth_client.post(reverse("notification-read", args=[foreign.pk])).status_code == 404


def test_read_all(auth_client, user):
    notify_many([user, user], title="bulk")

    assert auth_client.post(reverse("notification-read-all")).json() == {"count": 2}
    assert auth_client.get(reverse("notification-unread-count")).json() == {"count": 0}


def test_delete(auth_client, user):
    notification = notify(user, title="bye")

    assert auth_client.delete(reverse("notification-detail", args=[notification.pk])).status_code == 204
    assert auth_client.get(LIST_URL).json()["count"] == 0


def test_requires_auth(api_client):
    assert api_client.get(LIST_URL).status_code == 401
