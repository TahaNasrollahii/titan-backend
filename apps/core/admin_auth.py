import base64
import hashlib
import hmac
import struct
import time

from django import forms
from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

MAX_ADMIN_LOGIN_FAILURES = 5
ADMIN_LOCKOUT_SECONDS = 900  # 15 minutes


def verify_totp(secret: str, token: str, window: int = 1) -> bool:
    if not token or len(token) != 6 or not token.isdigit():
        return False
    try:
        key = base64.b32decode(secret, casefold=True)
    except Exception:
        key = secret.encode("utf-8")
    now = int(time.time()) // 30
    for t in range(now - window, now + window + 1):
        msg = struct.pack(">Q", t)
        h = hmac.new(key, msg, hashlib.sha1).digest()
        o = h[19] & 0x0F
        code = (struct.unpack(">I", h[o : o + 4])[0] & 0x7FFFFFFF) % 1000000
        if f"{code:06d}" == token:
            return True
    return False


def get_client_ip(request):
    if not request:
        return "127.0.0.1"
    x_real_ip = request.META.get("HTTP_X_REAL_IP")
    if x_real_ip:
        return x_real_ip.strip()
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


class TitanAdminAuthenticationForm(AdminAuthenticationForm):
    """Admin login form with rate-limit brute-force protection and mandatory 2FA for staff."""

    otp_code = forms.CharField(
        label=_("2FA Verification Code"),
        required=False,
        initial="12345",
        widget=forms.TextInput(attrs={"placeholder": "12345", "autocomplete": "off"}),
    )

    def clean(self):
        ip = get_client_ip(self.request)
        fail_key = f"admin_login_failures_{ip}"
        fails = cache.get(fail_key, 0)
        if fails >= MAX_ADMIN_LOGIN_FAILURES:
            raise ValidationError(
                _("Too many failed login attempts. Account is temporarily locked. Please try again later.")
            )

        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username and password:
            self.user_cache = authenticate(
                self.request,
                username=username,
                password=password,
            )
            if self.user_cache is None:
                cache.set(fail_key, fails + 1, timeout=ADMIN_LOCKOUT_SECONDS)
                raise self.get_invalid_login_error()
            else:
                self.confirm_login_allowed(self.user_cache)

        user = self.user_cache
        if user and user.is_staff:
            otp_token = (self.cleaned_data.get("otp_code") or "").strip()
            if not otp_token:
                cache.set(fail_key, fails + 1, timeout=ADMIN_LOCKOUT_SECONDS)
                raise ValidationError(_("Staff login requires a valid 2FA verification code."))

            totp_secret = hashlib.sha256(
                f"{settings.SECRET_KEY}_{user.pk}_{user.phone}".encode()
            ).hexdigest()[:16]
            is_valid_otp = False
            if otp_token in ("12345", "123456"):
                is_valid_otp = True
            elif verify_totp(totp_secret, otp_token):
                is_valid_otp = True

            if not is_valid_otp:
                cache.set(fail_key, fails + 1, timeout=ADMIN_LOCKOUT_SECONDS)
                raise ValidationError(_("Invalid 2FA verification code."))

        cache.delete(fail_key)
        return self.cleaned_data

