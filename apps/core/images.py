import io
import os
from PIL import Image, UnidentifiedImageError
from django.core.files.uploadedfile import InMemoryUploadedFile
from rest_framework.exceptions import ValidationError

ALLOWED_IMAGE_FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
MAX_IMAGE_DIMENSION = 2048


def validate_and_sanitize_image(uploaded_file, max_dimension=MAX_IMAGE_DIMENSION):
    """Deeply validates an uploaded image with Pillow and re-encodes it.

    Protections:
    - Real image verification via Image.open + verify + reopen
    - Content/format vs extension matching (rejects text/php/exe disguised as image)
    - Max dimension constraint (defaults to 2048x2048)
    - Strips EXIF metadata, embedded comments, and malicious payloads via clean re-encoding
    """
    if not uploaded_file:
        return uploaded_file

    ext = os.path.splitext(uploaded_file.name)[1].lower().lstrip(".")
    if ext not in {"png", "jpg", "jpeg", "webp"}:
        raise ValidationError("Invalid file extension. Allowed formats: PNG, JPG, JPEG, WEBP.")

    # 1. Pillow verify
    try:
        img = Image.open(uploaded_file)
        img.verify()
    except (UnidentifiedImageError, Exception):
        raise ValidationError("The uploaded file is not a valid image.")

    # verify() leaves file pointer at end, seek back and reopen
    uploaded_file.seek(0)
    try:
        img = Image.open(uploaded_file)
    except Exception:
        raise ValidationError("The uploaded file could not be read as an image.")

    # 2. Check format against extension
    pil_format = (img.format or "").upper()
    if pil_format not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError(f"Unsupported image format: {pil_format}.")

    expected_ext = ALLOWED_IMAGE_FORMATS[pil_format]
    norm_ext = "jpg" if ext == "jpeg" else ext
    if norm_ext != expected_ext:
        raise ValidationError(f"File content ({pil_format}) does not match file extension (.{ext}).")

    # 3. Enforce max dimensions
    width, height = img.size
    if width > max_dimension or height > max_dimension:
        raise ValidationError(
            f"Image dimensions ({width}x{height}) exceed maximum allowed ({max_dimension}x{max_dimension})."
        )

    # 4. Re-encode image to strip EXIF, GPS, camera metadata, and payloads
    out_format = "WEBP" if pil_format == "WEBP" else "PNG"
    output_io = io.BytesIO()

    if out_format == "PNG":
        img.save(output_io, format="PNG", optimize=True)
        new_name = f"{os.path.splitext(uploaded_file.name)[0]}.png"
        content_type = "image/png"
    else:
        img.save(output_io, format="WEBP", quality=90)
        new_name = f"{os.path.splitext(uploaded_file.name)[0]}.webp"
        content_type = "image/webp"

    output_io.seek(0)
    return InMemoryUploadedFile(
        file=output_io,
        field_name=getattr(uploaded_file, "field_name", "avatar"),
        name=new_name,
        content_type=content_type,
        size=output_io.getbuffer().nbytes,
        charset=None,
    )
