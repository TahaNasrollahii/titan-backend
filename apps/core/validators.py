from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _

image_extension_validator = FileExtensionValidator(allowed_extensions=["png", "jpg", "jpeg", "webp"])


@deconstructible
class MaxFileSizeValidator:
    def __init__(self, max_bytes: int | None = None):
        self.max_bytes = max_bytes

    def __call__(self, file):
        limit = self.max_bytes or settings.MAX_UPLOAD_SIZE
        if file.size > limit:
            raise ValidationError(
                _("File is too large. Maximum size is %(size)s MB."),
                code="file_too_large",
                params={"size": limit // (1024 * 1024)},
            )

    def __eq__(self, other):
        return isinstance(other, MaxFileSizeValidator) and self.max_bytes == other.max_bytes

    def __hash__(self):
        return hash(self.max_bytes)


IMAGE_VALIDATORS = [image_extension_validator, MaxFileSizeValidator()]
