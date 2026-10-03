"""Domain errors and the single place where every error is turned into an API response.

Error payload shape (camelCased by the renderer)::

    {"detail": "<localized message>", "code": "<machine code>", "errors": {...field errors}}
"""

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils.translation import gettext_lazy as _

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class DomainError(Exception):
    """A business rule was violated. Raised by services, rendered by the API layer."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = _("The request could not be completed.")
    default_code = "domain_error"

    def __init__(self, detail=None, code: str | None = None):
        self.detail = detail if detail is not None else self.default_detail
        self.code = code or self.default_code
        super().__init__(str(self.detail))


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


class NotAllowedError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = _("You are not allowed to perform this action.")
    default_code = "not_allowed"


class ExternalServiceError(DomainError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = _("An external service is unavailable. Please try again later.")
    default_code = "external_service_error"


def api_exception_handler(exc, context):
    if isinstance(exc, DomainError):
        return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)

    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, exceptions.ValidationError):
        errors = exc.detail if isinstance(exc.detail, dict) else {"non_field_errors": exc.detail}
        response.data = {"detail": _("Invalid input."), "code": "validation_error", "errors": errors}
    else:
        codes = exc.get_codes()
        detail = response.data.get("detail") if isinstance(response.data, dict) else None
        response.data = {
            "detail": detail or exc.default_detail,
            "code": codes if isinstance(codes, str) else exc.default_code,
        }
    return response
