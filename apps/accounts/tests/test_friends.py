from django.urls import reverse
from django.utils import timezone

import pytest

from apps.accounts.models import Friendship
from apps.accounts.tests.factories import UserFactory
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db

FRIENDS_URL = reverse("friend-list")
REQUESTS_URL = reverse("friend-request-list")


@pytest.fixture
def other(db):
    return UserFactory(username="NightWolf")


def test_send_friend_request_notifies_recipient(auth_client, other):
    response = auth_client.post(FRIENDS_URL, {"username": "nightwolf"}, format="json")

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    notification = Notification.objects.get(user=other)
    assert notification.kind == Notification.Kind.FRIEND_REQUEST
    assert notification.data["friendship_id"] == response.json()["id"]


def test_unknown_username(auth_client):
    response = auth_client.post(FRIENDS_URL, {"username": "ghost"}, format="json")

    assert response.status_code == 400


def test_cannot_befriend_self(auth_client, user):
    response = auth_client.post(FRIENDS_URL, {"username": user.username}, format="json")

    assert response.json()["code"] == "self_friendship"


def test_duplicate_request_conflicts(auth_client, other):
    auth_client.post(FRIENDS_URL, {"username": other.username})

    response = auth_client.post(FRIENDS_URL, {"username": other.username})

    assert response.status_code == 409
    assert response.json()["code"] == "request_pending"


def test_reciprocal_request_auto_accepts(auth_client, user, other):
    Friendship.objects.create(from_user=other, to_user=user)

    response = auth_client.post(FRIENDS_URL, {"username": other.username})

    assert response.json()["status"] == "accepted"


def test_accept_flow_and_friend_list_with_presence(auth_client, client_for, user, other):
    client_for(other).post(FRIENDS_URL, {"username": user.username})
    pending = auth_client.get(REQUESTS_URL).json()
    assert len(pending) == 1

    response = auth_client.post(f"{REQUESTS_URL}{pending[0]['id']}/accept/")
    other.last_seen = timezone.now()
    other.presence_status = "online"
    other.save()

    assert response.json()["status"] == "accepted"
    friends = auth_client.get(FRIENDS_URL).json()
    assert [(f["username"], f["presence"]) for f in friends] == [("NightWolf", "online")]
    assert client_for(other).get(FRIENDS_URL).json()[0]["username"] == user.username
    assert Notification.objects.filter(user=user, kind="friend_request", is_read=True).exists()


def test_decline(auth_client, client_for, user, other):
    client_for(other).post(FRIENDS_URL, {"username": user.username})
    request_id = auth_client.get(REQUESTS_URL).json()[0]["id"]

    assert auth_client.post(f"{REQUESTS_URL}{request_id}/decline/").json()["status"] == "declined"
    assert auth_client.get(FRIENDS_URL).json() == []


def test_cannot_accept_someone_elses_request(auth_client, user, other):
    third = UserFactory()
    friendship = Friendship.objects.create(from_user=other, to_user=third)

    response = auth_client.post(f"{REQUESTS_URL}{friendship.pk}/accept/")

    assert response.json()["code"] == "not_found"


def test_remove_friend(auth_client, user, other):
    Friendship.objects.create(from_user=user, to_user=other, status="accepted")

    assert auth_client.delete(f"{FRIENDS_URL}{other.username}/").status_code == 204
    assert not Friendship.objects.exists()


def test_remove_non_friend(auth_client, other):
    response = auth_client.delete(f"{FRIENDS_URL}{other.username}/")

    assert response.json()["code"] == "not_friends"


def test_declined_request_can_be_resent(auth_client, user, other):
    Friendship.objects.create(from_user=user, to_user=other, status="declined")

    response = auth_client.post(FRIENDS_URL, {"username": other.username})

    assert response.status_code == 201
    assert Friendship.objects.get().status == "pending"
