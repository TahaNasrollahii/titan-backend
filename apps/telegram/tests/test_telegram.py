import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlencode, urlparse

from django.urls import reverse

import pytest
import responses

from apps.accounts.tests.factories import UserFactory
from apps.core import channel
from apps.notifications.services import notify, notify_many
from apps.payments.models import Payment
from apps.payments.services import frontend_result_url
from apps.telegram import initdata, services
from apps.telegram.models import TelegramAccount

BOT_TOKEN = "123456:TEST-token"
WEBHOOK_SECRET = "s" * 40
MINIAPP = "https://app.titan.test"
TG_ID = 777000111


@pytest.fixture(autouse=True)
def telegram_settings(settings):
    settings.TELEGRAM_BOT_TOKEN = BOT_TOKEN
    settings.TELEGRAM_WEBHOOK_SECRET = WEBHOOK_SECRET
    settings.TELEGRAM_MINIAPP_URL = MINIAPP
    settings.TELEGRAM_INIT_DATA_MAX_AGE = 3600
    settings.OTP_TEST_CODE = ""


def sign(fields: dict, *, token: str = BOT_TOKEN) -> str:
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


def init_data(user_id: int = TG_ID, *, age: int = 0, token: str = BOT_TOKEN, **user) -> str:
    payload = {"id": user_id, "first_name": "Taha", "last_name": "N", "username": "taha", **user}
    return sign(
        {
            "auth_date": str(int(time.time()) - age),
            "query_id": "AAH",
            "user": json.dumps(payload),
            "signature": "ed25519-sig",
        },
        token=token,
    )


def signed_contact(phone: str, user_id: int = TG_ID) -> str:
    contact = {"user_id": user_id, "phone_number": phone, "first_name": "Taha"}
    return sign({"auth_date": str(int(time.time())), "contact": json.dumps(contact)})


# ------------------------------------------------------------------ signature verification
class TestInitData:
    def test_valid_data_is_parsed(self):
        parsed = initdata.parse_init_data(init_data(is_premium=True, allows_write_to_pm=True))
        assert parsed.user.id == TG_ID
        assert parsed.user.username == "taha"
        assert parsed.user.is_premium and parsed.user.allows_write_to_pm

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda raw: raw.replace("Taha", "Eve"),
            lambda raw: raw + "&extra=1",
            lambda raw: raw.rsplit("&hash=", 1)[0],
            lambda raw: raw + "&auth_date=1",
        ],
        ids=["tampered", "added-field", "no-hash", "duplicate-key"],
    )
    def test_tampering_is_rejected(self, mutate):
        with pytest.raises(initdata.InvalidInitData):
            initdata.parse_init_data(mutate(init_data()))

    def test_another_bots_signature_is_rejected(self):
        with pytest.raises(initdata.InvalidInitData):
            initdata.parse_init_data(init_data(token="999:other"))

    def test_expired_data_is_rejected(self):
        with pytest.raises(initdata.ExpiredInitData):
            initdata.parse_init_data(init_data(age=3601))

    def test_future_data_is_rejected(self):
        with pytest.raises(initdata.InvalidInitData):
            initdata.parse_init_data(init_data(age=-600))

    def test_without_bot_token_nothing_verifies(self, settings):
        settings.TELEGRAM_BOT_TOKEN = ""
        with pytest.raises(initdata.InvalidInitData):
            initdata.parse_init_data(init_data())

    def test_oversized_input_is_rejected(self):
        with pytest.raises(initdata.InvalidInitData):
            initdata.parse_init_data(init_data() + "&x=" + "a" * 5000)


# ------------------------------------------------------------------ sign-in & linking
@pytest.mark.django_db
class TestSignIn:
    def test_unlinked_user_is_asked_for_a_phone_number(self, api_client):
        response = api_client.post(reverse("telegram-auth"), {"initData": init_data()}, format="json")
        assert response.status_code == 200
        assert response.json()["linked"] is False
        assert response.json()["telegramUser"]["firstName"] == "Taha"
        assert "access" not in response.data

    def test_invalid_init_data_is_401(self, api_client):
        response = api_client.post(reverse("telegram-auth"), {"initData": "user=1&hash=00"}, format="json")
        assert response.status_code == 401
        assert response.json()["code"] == "telegram_init_data_invalid"

    def test_signed_contact_links_existing_account(self, api_client):
        user = UserFactory(phone="09121234567")
        response = api_client.post(
            reverse("telegram-auth-contact"),
            {"initData": init_data(), "contact": signed_contact("+989121234567")},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.json()["linked"] is True
        assert response.json()["isNewUser"] is False
        assert response.json()["user"]["id"] == user.pk
        assert response.json()["access"] and response.json()["refresh"]
        assert TelegramAccount.objects.get(user=user).telegram_id == TG_ID

        # From now on the launch data alone signs in.
        again = api_client.post(reverse("telegram-auth"), {"initData": init_data()}, format="json")
        assert again.json()["linked"] is True
        assert again.json()["user"]["id"] == user.pk

    def test_new_phone_creates_account_named_after_telegram(self, api_client):
        response = api_client.post(
            reverse("telegram-auth-contact"),
            {"initData": init_data(), "contact": signed_contact("989351112233")},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["isNewUser"] is True
        assert response.json()["user"]["phone"] == "09351112233"
        assert response.json()["user"]["fullName"] == "Taha N"

    def test_someone_elses_contact_is_refused(self, api_client):
        response = api_client.post(
            reverse("telegram-auth-contact"),
            {"initData": init_data(), "contact": signed_contact("09121234567", user_id=1)},
            format="json",
        )
        assert response.status_code == 403
        assert not TelegramAccount.objects.exists()

    def test_forged_contact_falls_back_to_webhook_copy_only(self, api_client):
        forged = signed_contact("09121234567").replace("0912", "0919")
        response = api_client.post(
            reverse("telegram-auth-contact"), {"initData": init_data(), "contact": forged}, format="json"
        )
        assert response.status_code == 409
        assert response.json()["code"] == "telegram_contact_pending"

    def test_contact_delivered_to_webhook_links(self, api_client):
        services.remember_contact(TG_ID, "+989121234567")
        response = api_client.post(reverse("telegram-auth-contact"), {"initData": init_data()}, format="json")
        assert response.status_code == 200
        assert response.json()["user"]["phone"] == "09121234567"

    def test_foreign_number_is_not_supported(self, api_client):
        response = api_client.post(
            reverse("telegram-auth-contact"),
            {"initData": init_data(), "contact": signed_contact("+447700900123")},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["code"] == "telegram_phone_not_supported"

    def test_disabled_account_cannot_sign_in(self, api_client):
        user = UserFactory(phone="09121234567", is_active=False)
        TelegramAccount.objects.create(user=user, telegram_id=TG_ID)
        response = api_client.post(reverse("telegram-auth"), {"initData": init_data()}, format="json")
        assert response.status_code == 403

    def test_relinking_moves_the_phone_to_the_new_telegram_user(self, api_client):
        user = UserFactory(phone="09121234567")
        TelegramAccount.objects.create(user=user, telegram_id=555)
        api_client.post(
            reverse("telegram-auth-contact"),
            {"initData": init_data(), "contact": signed_contact("09121234567")},
            format="json",
        )
        assert list(TelegramAccount.objects.values_list("telegram_id", flat=True)) == [TG_ID]

    def test_sms_code_fallback_links(self, api_client, settings):
        settings.SMS_BACKEND = "apps.accounts.sms.InMemorySMSBackend"
        from apps.accounts.sms import InMemorySMSBackend

        sent = api_client.post(
            reverse("telegram-code-request"), {"initData": init_data(), "phone": "09121234567"}, format="json"
        )
        assert sent.status_code == 201
        code = InMemorySMSBackend.outbox[-1][1]
        response = api_client.post(
            reverse("telegram-code-verify"),
            {"initData": init_data(), "phone": "09121234567", "code": code},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.json()["user"]["phone"] == "09121234567"

    def test_sms_code_needs_real_telegram_launch_data(self, api_client):
        response = api_client.post(
            reverse("telegram-code-request"),
            {"initData": "x=1&hash=0", "phone": "09121234567"},
            format="json",
        )
        assert response.status_code == 401


@pytest.mark.django_db
class TestAccountSettings:
    def test_toggle_notifications_and_unlink(self, client_for):
        user = UserFactory()
        TelegramAccount.objects.create(user=user, telegram_id=TG_ID)
        client = client_for(user)
        response = client.patch(
            reverse("telegram-me"), {"notificationsEnabled": False, "writeAccessGranted": True}, format="json"
        )
        assert response.status_code == 200
        assert response.json()["notificationsEnabled"] is False
        assert response.json()["canMessage"] is True

        assert client.delete(reverse("telegram-me")).status_code == 204
        assert not TelegramAccount.objects.exists()
        assert client.get(reverse("telegram-me")).status_code == 404


# ------------------------------------------------------------------ webhook
def post_update(api_client, update: dict, secret: str = WEBHOOK_SECRET):
    return api_client.post(
        reverse("telegram-webhook"),
        data=json.dumps(update),
        content_type="application/json",
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
    )


def private_message(**fields) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 5,
            "chat": {"id": TG_ID, "type": "private"},
            "from": {"id": TG_ID},
            **fields,
        },
    }


@pytest.mark.django_db
class TestWebhook:
    def test_wrong_secret_is_404(self, api_client):
        assert post_update(api_client, private_message(text="/start"), secret="nope").status_code == 404

    def test_disabled_without_secret(self, api_client, settings):
        settings.TELEGRAM_WEBHOOK_SECRET = ""
        assert post_update(api_client, private_message(text="/start"), secret="").status_code == 404

    def test_start_replies_with_open_app_button(self, api_client):
        response = post_update(api_client, private_message(text="/start"))
        assert response.status_code == 200
        reply = response.json()
        assert reply["method"] == "sendMessage"
        assert reply["chat_id"] == TG_ID
        assert reply["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == f"{MINIAPP}/"

    def test_own_contact_is_remembered_silently(self, api_client):
        response = post_update(
            api_client, private_message(contact={"user_id": TG_ID, "phone_number": "989121234567"})
        )
        assert response.status_code == 204
        assert (
            services.link_with_contact(initdata.parse_init_data(init_data()))[0].user.phone == "09121234567"
        )

    def test_forwarded_contact_is_ignored(self, api_client):
        post_update(api_client, private_message(contact={"user_id": 42, "phone_number": "989121234567"}))
        with pytest.raises(services.ContactNotReceived):
            services.link_with_contact(initdata.parse_init_data(init_data()))

    def test_block_and_unblock(self, api_client):
        account = TelegramAccount.objects.create(user=UserFactory(), telegram_id=TG_ID, can_message=True)
        update = {
            "update_id": 2,
            "my_chat_member": {
                "chat": {"id": TG_ID, "type": "private"},
                "from": {"id": TG_ID},
                "new_chat_member": {"status": "kicked"},
            },
        }
        post_update(api_client, update)
        account.refresh_from_db()
        assert account.can_message is False
        update["my_chat_member"]["new_chat_member"]["status"] = "member"
        post_update(api_client, update)
        account.refresh_from_db()
        assert account.can_message is True


# ------------------------------------------------------------------ notification push
@pytest.mark.django_db(transaction=True)
class TestPush:
    SEND_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    @responses.activate
    def test_notification_is_mirrored_with_deep_link(self):
        responses.post(self.SEND_URL, json={"ok": True, "result": {}})
        user = UserFactory()
        TelegramAccount.objects.create(user=user, telegram_id=TG_ID, can_message=True)
        notify(user, kind="order", title="Order <TN-1> delivered", data={"order_number": "TN-1"})

        assert len(responses.calls) == 1
        body = json.loads(responses.calls[0].request.body)
        assert body["chat_id"] == TG_ID
        assert "&lt;TN-1&gt;" in body["text"]
        assert body["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == f"{MINIAPP}/orders/TN-1"

    @responses.activate
    def test_opted_out_and_unreachable_users_are_skipped(self):
        responses.post(self.SEND_URL, json={"ok": True, "result": {}})
        muted = UserFactory()
        TelegramAccount.objects.create(
            user=muted, telegram_id=1, can_message=True, notifications_enabled=False
        )
        unreachable = UserFactory()
        TelegramAccount.objects.create(user=unreachable, telegram_id=2, can_message=False)
        notify_many([muted, unreachable, UserFactory()], title="Season starts")
        assert len(responses.calls) == 0

    @responses.activate
    def test_blocked_bot_marks_user_unreachable(self):
        responses.post(
            self.SEND_URL, status=403, json={"ok": False, "error_code": 403, "description": "bot was blocked"}
        )
        user = UserFactory()
        account = TelegramAccount.objects.create(user=user, telegram_id=TG_ID, can_message=True)
        notify(user, title="Hello")
        account.refresh_from_db()
        assert account.can_message is False


# ------------------------------------------------------------------ payments return to the Mini App
@pytest.mark.django_db
class TestPaymentChannel:
    def test_telegram_payment_returns_to_the_mini_app(self, settings):
        payment = Payment(pk=9, amount=1000, purpose=Payment.Purpose.WALLET_TOPUP, channel=channel.TELEGRAM)
        url = urlparse(frontend_result_url(payment))
        assert f"{url.scheme}://{url.netloc}" == MINIAPP
        assert parse_qs(url.query)["paymentId"] == ["9"]

    def test_web_payment_returns_to_the_website(self, settings):
        payment = Payment(pk=9, amount=1000, purpose=Payment.Purpose.WALLET_TOPUP)
        assert frontend_result_url(payment).startswith(settings.FRONTEND_URL)

    def test_header_marks_payments_started_from_the_mini_app(self, client_for, settings):
        settings.PAYMENT_GATEWAY = "fake"
        user = UserFactory()
        client_for(user).post(
            reverse("wallet-topup"), {"amount": 50_000}, format="json", HTTP_X_TITAN_CLIENT="telegram"
        )
        assert Payment.objects.get(user=user).channel == channel.TELEGRAM


# ------------------------------------------------------------------ live support from the Mini App
@pytest.mark.django_db
class TestSupportFromMiniApp:
    START = {"firstName": "Taha", "lastName": "Nasr", "phone": "09121234567"}

    def test_signed_in_users_skip_turnstile(self, api_client, client_for, settings):
        settings.TURNSTILE_SECRET_KEY = "turnstile-secret"
        anonymous = api_client.post(reverse("support-start"), self.START, format="json")
        assert anonymous.status_code == 400  # visitors still need the bot check
        signed_in = client_for(UserFactory()).post(reverse("support-start"), self.START, format="json")
        assert signed_in.status_code == 201

    def test_reply_to_an_away_user_becomes_a_notification(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.notifications.models import Notification
        from apps.support import services as support

        user = UserFactory()
        conversation, _token = support.start_conversation(
            first_name="Taha", last_name="N", phone=user.phone, user=user
        )
        conversation.visitor_last_seen = timezone.now() - timedelta(minutes=10)
        conversation.save(update_fields=["visitor_last_seen"])

        assert support.notify_visitor_if_away(conversation) is True
        notification = Notification.objects.get(user=user)
        assert notification.data == {"support_ticket": conversation.ticket}
        assert services.notification_path(notification) == "/support"
