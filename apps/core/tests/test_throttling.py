import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.core.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    SimpleRateThrottle,
    TrustedProxyThrottleMixin,
    UserRateThrottle,
)

pytestmark = pytest.mark.django_db


from rest_framework.permissions import AllowAny


class DummyAnonView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def get(self, request):
        return Response({"status": "ok"})


class DummyScopedView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_ip"

    def post(self, request):
        return Response({"status": "ok"})


def test_throttle_ident_prefers_x_real_ip():
    factory = APIRequestFactory()
    request = factory.get(
        "/api/v1/store/",
        HTTP_X_REAL_IP=" 203.0.113.10 ",
        HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2",
    )
    throttle = AnonRateThrottle()
    assert throttle.get_ident(request) == "203.0.113.10"


def test_throttle_ident_extracts_rightmost_x_forwarded_for():
    factory = APIRequestFactory()
    # Attacker tries to spoof IP by prepending 8.8.8.8 to X-Forwarded-For
    request = factory.get(
        "/api/v1/store/",
        HTTP_X_FORWARDED_FOR="8.8.8.8, 10.0.0.1, 203.0.113.50",
    )
    throttle = AnonRateThrottle()
    assert throttle.get_ident(request) == "203.0.113.50"


def test_throttle_ident_falls_back_to_remote_addr():
    factory = APIRequestFactory()
    request = factory.get("/api/v1/store/", REMOTE_ADDR="192.168.1.100")
    throttle = AnonRateThrottle()
    assert throttle.get_ident(request) == "192.168.1.100"


def test_throttle_enforces_limits_and_returns_429(monkeypatch):
    from rest_framework.settings import api_settings

    monkeypatch.setitem(api_settings.DEFAULT_THROTTLE_RATES, "otp_ip", "2/hour")
    factory = APIRequestFactory()
    view = DummyScopedView.as_view()

    # Request 1 from client A -> 200
    req1 = factory.post("/api/v1/auth/otp/request/", HTTP_X_REAL_IP="198.51.100.1")
    resp1 = view(req1)
    assert resp1.status_code == 200

    # Request 2 from client A -> 200
    req2 = factory.post("/api/v1/auth/otp/request/", HTTP_X_REAL_IP="198.51.100.1")
    resp2 = view(req2)
    assert resp2.status_code == 200

    # Request 3 from client A -> 429 Too Many Requests
    req3 = factory.post("/api/v1/auth/otp/request/", HTTP_X_REAL_IP="198.51.100.1")
    resp3 = view(req3)
    assert resp3.status_code == 429

    # Request from client B (different IP) -> should succeed (200), not throttled by client A
    req_b = factory.post("/api/v1/auth/otp/request/", HTTP_X_REAL_IP="198.51.100.2")
    resp_b = view(req_b)
    assert resp_b.status_code == 200


def test_base_settings_cache_config():
    from config.settings import base

    assert "default" in base.CACHES
    assert (
        "DatabaseCache" in base.CACHES["default"]["BACKEND"]
        or "RedisCache" in base.CACHES["default"]["BACKEND"]
    )
    assert base.CACHES["default"]["LOCATION"] == "django_cache_table" or "redis" in base.CACHES["default"]["LOCATION"]

