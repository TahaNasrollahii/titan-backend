from datetime import timedelta
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.urls import reverse
from django.utils import timezone

import pytest
from PIL import Image

from apps.accounts.models import GameAccount, User
from apps.accounts.services import award_progress
from apps.accounts.sms import InMemorySMSBackend
from apps.accounts.tests.factories import BadgeFactory, GameAccountFactory, UserFactory
from apps.catalog.tests.factories import GameFactory

pytestmark = pytest.mark.django_db


def png_file(name="avatar.png", size=(32, 32)) -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class TestMe:
    def test_requires_authentication(self, api_client):
        assert api_client.get(reverse("me")).status_code == 401

    def test_returns_profile_with_rank_and_badges(self, auth_client, user, rank_tiers):
        user.points = 2000
        user.save()
        badge = BadgeFactory(slug="champion", name="Champion")
        user.user_badges.create(badge=badge)

        body = auth_client.get(reverse("me")).json()

        assert body["phone"] == user.phone
        assert body["rank"]["slug"] == "silver"
        assert body["badges"] == [{"slug": "champion", "name": "Champion", "description": "", "icon": ""}]
        assert body["maxXp"] == 800

    def test_update_profile(self, auth_client):
        game = GameFactory(slug="fortnite")

        response = auth_client.patch(
            reverse("me"),
            {
                "fullName": "Taha",
                "email": "taha@example.com",
                "username": "TahaTitan",
                "favoriteGame": "fortnite",
            },
            format="json",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["fullName"] == "Taha"
        assert body["favoriteGame"] == "fortnite"
        assert body["favoriteGameDetail"]["slug"] == game.slug

    def test_cannot_change_read_only_fields(self, auth_client, user):
        auth_client.patch(
            reverse("me"), {"points": 99999, "phone": "09999999999", "isStaff": True}, format="json"
        )

        user.refresh_from_db()
        assert user.points == 0
        assert user.phone != "09999999999"
        assert not user.is_staff

    def test_username_must_be_unique_case_insensitive(self, auth_client):
        UserFactory(username="TakenName")

        response = auth_client.patch(reverse("me"), {"username": "takenname"}, format="json")

        assert response.status_code == 400
        assert "username" in response.json()["errors"]

    def test_username_format_is_validated(self, auth_client):
        response = auth_client.patch(reverse("me"), {"username": "bad name!"}, format="json")

        assert response.status_code == 400

    def test_avatar_upload(self, auth_client):
        response = auth_client.post(reverse("me-avatar"), {"avatar": png_file()}, format="multipart")

        assert response.status_code == 200
        assert response.json()["avatar"].endswith(".png")

    def test_avatar_rejects_non_images(self, auth_client):
        bogus = SimpleUploadedFile("avatar.txt", b"hello", content_type="text/plain")

        response = auth_client.post(reverse("me-avatar"), {"avatar": bogus}, format="multipart")

        assert response.status_code == 400

    def test_avatar_rejects_large_files(self, auth_client, settings):
        settings.MAX_UPLOAD_SIZE = 100

        response = auth_client.post(
            reverse("me-avatar"), {"avatar": png_file(size=(200, 200))}, format="multipart"
        )

        assert response.status_code == 400


class TestPhoneChange:
    NEW_PHONE = "09351234567"

    def test_change_phone_with_otp(self, auth_client, user):
        assert auth_client.post(reverse("phone-change-request"), {"phone": self.NEW_PHONE}).status_code == 201
        code = InMemorySMSBackend.last_code_for(self.NEW_PHONE)

        response = auth_client.post(reverse("phone-change-verify"), {"phone": self.NEW_PHONE, "code": code})

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.phone == self.NEW_PHONE

    def test_login_code_cannot_change_phone(self, api_client, auth_client):
        api_client.post(reverse("otp-request"), {"phone": self.NEW_PHONE})
        login_code = InMemorySMSBackend.last_code_for(self.NEW_PHONE)

        response = auth_client.post(
            reverse("phone-change-verify"), {"phone": self.NEW_PHONE, "code": login_code}
        )

        assert response.status_code == 400

    def test_phone_already_taken(self, auth_client):
        UserFactory(phone=self.NEW_PHONE)

        response = auth_client.post(
            reverse("phone-change-verify"), {"phone": self.NEW_PHONE, "code": "12345"}
        )

        assert response.status_code == 409
        assert response.json()["code"] == "phone_taken"


class TestGameAccounts:
    url = reverse("game-account-list")

    def test_create_never_returns_password(self, auth_client):
        response = auth_client.post(
            self.url, {"title": "Epic", "username": "me@epic.com", "password": "hunter2"}, format="json"
        )

        assert response.status_code == 201
        assert "password" not in response.json()
        assert response.json()["hasPassword"] is True

    def test_password_is_encrypted_at_rest(self, auth_client):
        auth_client.post(self.url, {"title": "Epic", "username": "me@epic.com", "password": "hunter2"})

        with connection.cursor() as cursor:
            cursor.execute("SELECT password FROM accounts_gameaccount")
            stored = cursor.fetchone()[0]
        assert stored != "hunter2"
        assert GameAccount.objects.get().password == "hunter2"

    def test_password_required_on_create(self, auth_client):
        response = auth_client.post(self.url, {"title": "Epic", "username": "me@epic.com"})

        assert response.status_code == 400
        assert "password" in response.json()["errors"]

    def test_blank_password_on_update_keeps_existing(self, auth_client, user):
        account = GameAccountFactory(user=user, password="original")

        auth_client.patch(f"{self.url}{account.pk}/", {"title": "Renamed", "password": ""}, format="json")

        account.refresh_from_db()
        assert account.title == "Renamed"
        assert account.password == "original"

    def test_users_only_see_their_own_accounts(self, auth_client, user):
        GameAccountFactory(user=user)
        other = GameAccountFactory()

        assert len(auth_client.get(self.url).json()) == 1
        assert auth_client.get(f"{self.url}{other.pk}/").status_code == 404
        assert auth_client.delete(f"{self.url}{other.pk}/").status_code == 404

    def test_delete(self, auth_client, user):
        account = GameAccountFactory(user=user)

        assert auth_client.delete(f"{self.url}{account.pk}/").status_code == 204
        assert not GameAccount.objects.exists()


class TestPresence:
    def test_heartbeat_sets_presence(self, auth_client, user):
        game = GameFactory(slug="valorant")

        response = auth_client.post(
            reverse("heartbeat"), {"status": "in_game", "game": "valorant"}, format="json"
        )

        assert response.status_code == 204
        user.refresh_from_db()
        assert user.presence == User.Presence.IN_GAME
        assert user.current_game == game

    def test_presence_times_out_to_offline(self, user):
        user.last_seen = timezone.now() - timedelta(minutes=10)

        assert user.presence == User.Presence.OFFLINE

    def test_never_seen_is_offline(self, user):
        assert user.presence == User.Presence.OFFLINE


class TestProgress:
    def test_award_progress_levels_up(self, user):
        user = award_progress(user, xp=2000, points=50)

        # level 1 needs 800 xp, level 2 needs 1100 → 2000 - 800 - 1100 = 100 left at level 3
        assert (user.level, user.xp, user.points) == (3, 100, 50)


class TestRanks:
    def test_rank_list(self, api_client, rank_tiers):
        body = api_client.get(reverse("rank-list")).json()

        assert [tier["slug"] for tier in body] == ["bronze", "silver", "gold"]
