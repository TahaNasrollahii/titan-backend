"""Which app a request came from: the website or the Telegram Mini App.

The Mini App sends ``X-Titan-Client: telegram``. Like the active language, the value is request-scoped so
services that outlive the request in other ways (e.g. a payment that later returns from the gateway) can
record it without every view passing it down.
"""

from contextvars import ContextVar

WEB = "web"
TELEGRAM = "telegram"
CHANNELS = (WEB, TELEGRAM)

HEADER = "X-Titan-Client"

_current: ContextVar[str] = ContextVar("titan_client_channel", default=WEB)


def current() -> str:
    return _current.get()


class ClientChannelMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        value = request.headers.get(HEADER, "").strip().lower()
        token = _current.set(value if value in CHANNELS else WEB)
        try:
            return self.get_response(request)
        finally:
            _current.reset(token)
