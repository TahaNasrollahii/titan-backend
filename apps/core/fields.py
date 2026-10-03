"""Model fields shared across apps."""

from functools import cache

from django.conf import settings
from django.db import models

from cryptography.fernet import Fernet, InvalidToken


@cache
def _fernet() -> Fernet:
    return Fernet(settings.FIELD_ENCRYPTION_KEY.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt field value; check FIELD_ENCRYPTION_KEY.") from exc


class EncryptedTextField(models.TextField):
    """Transparently Fernet-encrypts its value at rest.

    In Python the attribute always holds plaintext; the database only ever sees ciphertext.
    Encrypted values cannot be filtered or ordered on.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        return encrypt(value)

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        return decrypt(value)
