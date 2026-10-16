"""Trusted proxy rate throttling for serverless deployments (Vercel)."""

from rest_framework.throttling import (
    AnonRateThrottle as BaseAnonRateThrottle,
    ScopedRateThrottle as BaseScopedRateThrottle,
    SimpleRateThrottle as BaseSimpleRateThrottle,
    UserRateThrottle as BaseUserRateThrottle,
)


class TrustedProxyThrottleMixin:
    """Accurately extracts client IP behind Vercel edge reverse proxies.

    Prevents clients from spoofing their IP via forged X-Forwarded-For prefixes.
    Vercel sets X-Real-IP and appends the connecting client IP as the last entry
    in X-Forwarded-For.
    """

    def get_ident(self, request):
        x_real_ip = request.META.get("HTTP_X_REAL_IP")
        if x_real_ip:
            return x_real_ip.strip()

        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                return parts[-1]

        return request.META.get("REMOTE_ADDR", "127.0.0.1")


class AnonRateThrottle(TrustedProxyThrottleMixin, BaseAnonRateThrottle):
    pass


class UserRateThrottle(TrustedProxyThrottleMixin, BaseUserRateThrottle):
    pass


class ScopedRateThrottle(TrustedProxyThrottleMixin, BaseScopedRateThrottle):
    pass


class SimpleRateThrottle(TrustedProxyThrottleMixin, BaseSimpleRateThrottle):
    pass

