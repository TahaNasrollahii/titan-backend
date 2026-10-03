import re
import secrets
import string

from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

_LOCAL_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_IRAN_MOBILE = re.compile(r"^09\d{9}$")


def normalize_digits(value: str) -> str:
    """Convert Persian/Arabic-Indic digits to ASCII digits."""
    return value.translate(_LOCAL_DIGITS)


def normalize_phone(value: str) -> str:
    """Normalize an Iranian mobile number to the ``09xxxxxxxxx`` form.

    Accepts ``+989...``, ``00989...``, ``989...``, ``9...`` and Persian digits.
    """
    digits = re.sub(r"\D", "", normalize_digits(value or ""))
    if digits.startswith("0098"):
        digits = digits[4:]
    elif digits.startswith("98") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    if not _IRAN_MOBILE.match(digits):
        raise serializers.ValidationError(_("Enter a valid Iranian mobile number."), code="invalid_phone")
    return digits


def random_code(length: int, alphabet: str = string.ascii_uppercase + string.digits) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))


def percent_off(price: int, original_price: int | None) -> int:
    if not original_price or original_price <= price:
        return 0
    return round((original_price - price) * 100 / original_price)
