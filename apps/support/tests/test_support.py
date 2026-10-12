import uuid
from datetime import timedelta
from io import BytesIO

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

import pytest
from PIL import Image

from apps.accounts.sms import InMemorySMSBackend
from apps.accounts.tests.factories import UserFactory
from apps.support import services
from apps.support.models import CannedReply, Conversation, Message, SupportAgent

pytestmark = pytest.mark.django_db

START = {"firstName": "طاها", "lastName": "رضایی", "phone": "۰۹۱۲۳۴۵۶۷۸۹"}


def png(name="shot.png") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def start(client, **overrides) -> tuple[str, dict]:
    response = client.post(reverse("support-start"), {**START, **overrides}, format="json")
    assert response.status_code == 201, response.json()
    body = response.json()
    return body["token"], body


def visitor(client, token):
    client.credentials(HTTP_X_SUPPORT_TOKEN=token)
    return client


def online_agent(**kwargs) -> SupportAgent:
    user = UserFactory(is_staff=True, full_name="سارا محمدی", **kwargs)
    return SupportAgent.objects.create(user=user, is_available=True, last_seen=timezone.now())


# ------------------------------------------------------------------ public status
def test_status_reports_online_agents(api_client):
    assert api_client.get(reverse("support-status")).json() == {
        "online": False,
        "agents": [],
        "avgResponseSeconds": None,
    }
    online_agent()
    SupportAgent.objects.create(user=UserFactory(is_staff=True), is_available=False, last_seen=timezone.now())
    SupportAgent.objects.create(
        user=UserFactory(is_staff=True),
        is_available=True,
        last_seen=timezone.now() - timedelta(minutes=5),
    )

    body = api_client.get(reverse("support-status")).json()

    assert body["online"] is True
    assert [agent["name"] for agent in body["agents"]] == ["سارا"]


# ------------------------------------------------------------------ visitor
def test_start_normalizes_input_and_returns_token(api_client):
    token, body = start(api_client, firstName="  طاها  ", pageUrl="/store")

    conversation = Conversation.objects.get()
    assert conversation.phone == "09123456789"
    assert conversation.first_name == "طاها"
    assert conversation.page_url == "/store"
    assert conversation.token_hash == Conversation.hash_token(token)
    assert token not in conversation.token_hash
    assert body["conversation"]["ticket"] == conversation.ticket
    assert body["conversation"]["status"] == "open"
    assert body["messages"] == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("phone", "12345"), ("firstName", "a"), ("lastName", "123"), ("firstName", "<b>x</b>")],
)
def test_start_validation(api_client, field, value):
    response = api_client.post(reverse("support-start"), {**START, field: value}, format="json")

    assert response.status_code == 400
    assert field in response.json()["errors"]


def test_start_links_logged_in_user(auth_client, user):
    start(auth_client)

    assert Conversation.objects.get().user == user


def test_endpoints_need_a_valid_token(api_client):
    assert api_client.get(reverse("support-sync")).json()["code"] == "conversation_not_found"
    visitor(api_client, "nope")
    assert api_client.get(reverse("support-sync")).status_code == 404


def test_visitor_message_flow_and_sync_cursor(api_client):
    token, _ = start(api_client)
    client = visitor(api_client, token)

    first = client.post(reverse("support-messages"), {"body": " سلام "}, format="json")
    second = client.post(reverse("support-messages"), {"body": "کد کار نمی‌کنه"}, format="json")

    assert first.status_code == 201
    assert first.json()["body"] == "سلام"
    everything = client.get(reverse("support-sync")).json()["messages"]
    assert [m["body"] for m in everything] == ["سلام", "کد کار نمی‌کنه"]
    newer = client.get(reverse("support-sync"), {"after": first.json()["id"]}).json()["messages"]
    assert [m["id"] for m in newer] == [second.json()["id"]]
    conversation = Conversation.objects.get()
    assert conversation.awaiting_since is not None
    assert conversation.visitor_read_id == second.json()["id"]


def test_client_id_makes_sending_idempotent(api_client):
    token, _ = start(api_client)
    client = visitor(api_client, token)
    payload = {"body": "retry me", "clientId": str(uuid.uuid4())}

    first = client.post(reverse("support-messages"), payload, format="json")
    again = client.post(reverse("support-messages"), payload, format="json")

    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    assert Message.objects.count() == 1


def test_empty_message_is_rejected(api_client):
    token, _ = start(api_client)

    response = visitor(api_client, token).post(reverse("support-messages"), {"body": "   "}, format="json")

    assert response.status_code == 400
    assert "body" in response.json()["errors"]


def test_image_attachment(api_client):
    token, _ = start(api_client)

    response = visitor(api_client, token).post(
        reverse("support-messages"), {"image": png(), "clientId": str(uuid.uuid4())}, format="multipart"
    )

    assert response.status_code == 201
    image = response.json()["image"]
    assert image.startswith("http://testserver/media/support/")
    assert image.endswith(".png")
    assert "shot" not in image  # random file names


def test_non_image_attachment_is_rejected(api_client):
    token, _ = start(api_client)
    bogus = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")

    response = visitor(api_client, token).post(
        reverse("support-messages"), {"image": bogus}, format="multipart"
    )

    assert response.status_code == 400


def test_internal_notes_are_hidden_from_the_visitor(api_client, staff_user, client_for):
    token, _ = start(api_client)
    conversation = Conversation.objects.get()
    desk = client_for(staff_user)
    desk.post(
        reverse("desk-messages", args=[conversation.pk]), {"body": "VIP", "internal": True}, format="json"
    )
    desk.post(reverse("desk-messages", args=[conversation.pk]), {"body": "سلام!"}, format="json")

    bodies = [m["body"] for m in visitor(api_client, token).get(reverse("support-sync")).json()["messages"]]

    assert "VIP" not in bodies
    assert bodies[-1] == "سلام!"


def test_typing_and_read_receipts(api_client, staff_user, client_for):
    token, _ = start(api_client)
    client = visitor(api_client, token)
    sent = client.post(reverse("support-messages"), {"body": "hi"}, format="json").json()
    conversation = Conversation.objects.get()
    desk = client_for(staff_user)

    desk.post(reverse("desk-typing", args=[conversation.pk]))
    desk.post(reverse("desk-read", args=[conversation.pk]), {"lastId": sent["id"]}, format="json")
    state = client.get(reverse("support-sync")).json()["conversation"]

    assert state["agentTyping"] is True
    assert state["agentReadId"] == sent["id"]


def test_read_marker_only_moves_forward_and_stops_at_newest(api_client):
    token, _ = start(api_client)
    client = visitor(api_client, token)
    message = client.post(reverse("support-messages"), {"body": "hi"}, format="json").json()

    client.post(reverse("support-read"), {"lastId": 999_999}, format="json")
    client.post(reverse("support-read"), {"lastId": 0}, format="json")

    assert Conversation.objects.get().visitor_read_id == message["id"]


def test_close_then_rate_once(api_client):
    token, _ = start(api_client)
    client = visitor(api_client, token)

    early = client.post(reverse("support-rate"), {"rating": 5}, format="json")
    closed = client.post(reverse("support-close")).json()
    rated = client.post(reverse("support-rate"), {"rating": 4, "comment": "خوب بود"}, format="json")
    twice = client.post(reverse("support-rate"), {"rating": 1}, format="json")
    late = client.post(reverse("support-messages"), {"body": "hello?"}, format="json")

    assert early.json()["code"] == "conversation_open"
    assert closed["status"] == "closed"
    assert rated.json()["rating"] == 4
    assert twice.json()["code"] == "already_rated"
    assert late.status_code == 409
    assert late.json()["code"] == "conversation_closed"
    assert Message.objects.filter(kind=Message.Kind.SYSTEM).count() == 1


@pytest.mark.parametrize("rating", [0, 6])
def test_rating_range(api_client, rating):
    token, _ = start(api_client)
    client = visitor(api_client, token)
    client.post(reverse("support-close"))

    assert client.post(reverse("support-rate"), {"rating": rating}, format="json").status_code == 400


def test_visitor_throttle_is_per_token(api_client, settings):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "support_send": "2/min",
        },
    }
    from apps.support.views import SendThrottle

    SendThrottle.THROTTLE_RATES = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    try:
        token_a, _ = start(api_client)
        token_b, _ = start(api_client)
        a = visitor(api_client, token_a)
        statuses = [
            a.post(reverse("support-messages"), {"body": str(n)}, format="json").status_code for n in range(3)
        ]
        b = visitor(api_client, token_b)
        assert statuses == [201, 201, 429]
        assert b.post(reverse("support-messages"), {"body": "x"}, format="json").status_code == 201
    finally:
        from rest_framework.settings import api_settings

        SendThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES


# ------------------------------------------------------------------ desk
def test_desk_is_staff_only(auth_client, api_client):
    assert auth_client.get(reverse("desk-sync")).status_code == 403
    assert api_client.get(reverse("desk-sync")).status_code == 401


def test_first_reply_claims_and_announces(api_client, staff_user, client_for):
    staff_user.full_name = "سارا محمدی"
    staff_user.save()
    token, _ = start(api_client)
    visitor(api_client, token).post(reverse("support-messages"), {"body": "سلام"}, format="json")
    conversation = Conversation.objects.get()

    reply = client_for(staff_user).post(
        reverse("desk-messages", args=[conversation.pk]), {"body": "در خدمتم"}, format="json"
    )

    conversation.refresh_from_db()
    assert reply.status_code == 201
    assert reply.json()["author"]["name"] == "سارا"
    assert conversation.assignee == staff_user
    assert conversation.awaiting_since is None
    kinds = list(conversation.messages.values_list("kind", "body"))
    assert kinds[-2:] == [("system", "سارا به گفتگو پیوست"), ("agent", "در خدمتم")]


def test_desk_sync_queue_and_conversation(api_client, staff_user, client_for):
    token, _ = start(api_client)
    client = visitor(api_client, token)
    client.post(reverse("support-messages"), {"body": "one"}, format="json")
    client.post(reverse("support-messages"), {"image": png()}, format="multipart")
    conversation = Conversation.objects.get()
    desk = client_for(staff_user)

    body = desk.get(reverse("desk-sync"), {"conversation": conversation.pk}).json()

    item = body["queue"][0]
    assert item["ticket"] == conversation.ticket
    assert item["unread"] == 2
    assert item["lastHasImage"] is True
    assert item["visitorOnline"] is True
    assert body["conversation"]["firstName"] == "طاها"
    assert body["customer"]["previousConversations"] == 0
    assert [m["body"] for m in body["messages"]] == ["one", ""]
    assert body["me"]["id"] == staff_user.pk
    later = desk.get(reverse("desk-sync"), {"conversation": conversation.pk, "after": 10**9}).json()
    assert later["customer"] is None
    assert later["messages"] == []


def test_desk_customer_card_for_members(api_client, user, client_for, staff_user):
    from apps.orders.models import Order

    Order.objects.create(user=user, number="ORD-1", subtotal=100, total=100)
    client = client_for(user)
    start(client)
    conversation = Conversation.objects.get()

    customer = (
        client_for(staff_user).get(reverse("desk-sync"), {"conversation": conversation.pk}).json()["customer"]
    )

    assert customer["username"] == user.username
    assert customer["ordersCount"] == 1
    assert customer["recentOrders"][0]["number"] == "ORD-1"


def test_queue_orders_waiting_first_and_hides_closed(api_client, staff_user, client_for):
    token_a, _ = start(api_client)
    token_b, _ = start(api_client)
    token_c, _ = start(api_client)
    visitor(api_client, token_a).post(reverse("support-messages"), {"body": "a"}, format="json")
    visitor(api_client, token_b).post(reverse("support-messages"), {"body": "b"}, format="json")
    visitor(api_client, token_c).post(reverse("support-close"))
    a, b, _c = Conversation.objects.order_by("id")

    queue = client_for(staff_user).get(reverse("desk-sync")).json()["queue"]

    assert [item["id"] for item in queue] == [a.pk, b.pk]


def test_assign_claim_transfer_and_release(api_client, staff_user, client_for):
    colleague = UserFactory(is_staff=True, full_name="مهدی کریمی")
    start(api_client)
    conversation = Conversation.objects.get()
    me, other = client_for(staff_user), client_for(colleague)
    url = reverse("desk-assign", args=[conversation.pk])

    assert me.post(url, {"agent": staff_user.pk}, format="json").json()["assignee"]["id"] == staff_user.pk
    assert other.post(url, {"agent": colleague.pk}, format="json").json()["code"] == "already_assigned"
    assert me.post(url, {"agent": colleague.pk}, format="json").json()["assignee"]["id"] == colleague.pk
    assert other.post(url, {"agent": None}, format="json").json()["assignee"] is None
    events = list(conversation.messages.values_list("kind", "body"))
    assert events[1] == ("system", "گفتگو به مهدی منتقل شد")
    assert events[2][0] == "note"


def test_assign_rejects_non_staff(api_client, staff_user, client_for, user):
    start(api_client)
    conversation = Conversation.objects.get()

    response = client_for(staff_user).post(
        reverse("desk-assign", args=[conversation.pk]), {"agent": user.pk}, format="json"
    )

    assert response.status_code == 400


def test_agent_close_and_notes_after_close(api_client, staff_user, client_for):
    start(api_client)
    conversation = Conversation.objects.get()
    desk = client_for(staff_user)

    closed = desk.post(reverse("desk-close", args=[conversation.pk])).json()
    reply = desk.post(reverse("desk-messages", args=[conversation.pk]), {"body": "x"}, format="json")
    note = desk.post(
        reverse("desk-messages", args=[conversation.pk]),
        {"body": "follow up", "internal": True},
        format="json",
    )

    assert closed["status"] == "closed"
    assert reply.status_code == 409
    assert note.status_code == 201


def test_desk_me_toggles_availability(staff_user, client_for):
    desk = client_for(staff_user)

    body = desk.patch(reverse("desk-me"), {"isAvailable": True, "displayName": "سارا"}, format="json").json()
    desk.get(reverse("desk-sync"))

    assert body["isAvailable"] is True
    assert body["name"] == "سارا"
    assert SupportAgent.objects.online().count() == 1


def test_desk_history_search(api_client, staff_user, client_for):
    start(api_client)
    start(api_client, firstName="مریم", phone="09351112233")
    second = Conversation.objects.get(first_name="مریم")
    desk = client_for(staff_user)

    by_name = desk.get(reverse("desk-conversations"), {"search": "مریم"}).json()["results"]
    by_ticket = desk.get(reverse("desk-conversations"), {"search": second.ticket.lower()}).json()["results"]
    by_phone = desk.get(reverse("desk-conversations"), {"search": "0935"}).json()["results"]

    assert [r["id"] for r in by_name] == [second.pk]
    assert [r["id"] for r in by_ticket] == [second.pk]
    assert [r["id"] for r in by_phone] == [second.pk]


def test_canned_replies_crud(staff_user, client_for, auth_client):
    desk = client_for(staff_user)
    url = reverse("support-canned-reply-list")

    created = desk.post(
        url, {"shortcut": "سلام", "title": "Greeting", "body": "سلام! چطور کمکت کنم؟"}, format="json"
    )
    listed = desk.get(url).json()
    removed = desk.delete(reverse("support-canned-reply-detail", args=[created.json()["id"]]))

    assert created.status_code == 201
    assert [r["shortcut"] for r in listed] == ["سلام"]
    assert removed.status_code == 204
    assert not CannedReply.objects.exists()
    assert auth_client.get(url).status_code == 403


# ------------------------------------------------------------------ SMS
def test_agents_are_texted_once_when_nobody_is_online(api_client):
    agent = SupportAgent.objects.create(user=UserFactory(is_staff=True, phone="09120000001"))
    SupportAgent.objects.create(user=UserFactory(is_staff=True), sms_alerts=False)
    token, _ = start(api_client)
    client = visitor(api_client, token)

    client.post(reverse("support-messages"), {"body": "one"}, format="json")
    client.post(reverse("support-messages"), {"body": "two"}, format="json")

    ticket = Conversation.objects.get().ticket
    assert InMemorySMSBackend.notices == [("support_alert", agent.user.phone, ticket)]


def test_no_alert_while_an_agent_is_online(api_client):
    online_agent()
    token, _ = start(api_client)

    visitor(api_client, token).post(reverse("support-messages"), {"body": "hi"}, format="json")

    assert InMemorySMSBackend.notices == []


def test_alert_cooldown_per_agent(api_client):
    SupportAgent.objects.create(user=UserFactory(is_staff=True))
    for _ in range(2):
        token, _ = start(api_client)
        visitor(api_client, token).post(reverse("support-messages"), {"body": "hi"}, format="json")

    assert len(InMemorySMSBackend.notices) == 1


def test_visitor_is_texted_when_away(api_client, staff_user, client_for):
    start(api_client)
    conversation = Conversation.objects.get()
    Conversation.objects.update(visitor_last_seen=timezone.now() - timedelta(minutes=5))
    desk = client_for(staff_user)

    desk.post(reverse("desk-messages", args=[conversation.pk]), {"body": "answer"}, format="json")
    desk.post(reverse("desk-messages", args=[conversation.pk]), {"body": "more"}, format="json")

    assert InMemorySMSBackend.notices == [("support_reply", "09123456789", conversation.ticket)]


def test_visitor_present_is_not_texted(api_client, staff_user, client_for):
    start(api_client)
    conversation = Conversation.objects.get()

    client_for(staff_user).post(
        reverse("desk-messages", args=[conversation.pk]), {"body": "hi"}, format="json"
    )

    assert InMemorySMSBackend.notices == []


# ------------------------------------------------------------------ stats
def test_average_response_seconds(api_client, staff_user):
    start(api_client)
    conversation = Conversation.objects.get()
    services.post_message(conversation, kind=Message.Kind.VISITOR, body="q")
    services.post_message(conversation, kind=Message.Kind.AGENT, author=staff_user, body="a")
    Message.objects.filter(kind=Message.Kind.AGENT).update(
        created_at=Message.objects.get(kind=Message.Kind.VISITOR).created_at + timedelta(seconds=90)
    )

    cache.clear()  # the start response already cached the empty statistic

    assert services.average_response_seconds() == 90
