from rest_framework import serializers


class ImageUrlField(serializers.ImageField):
    """Read-only image field that renders an absolute URL (or ``None`` when empty)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("read_only", True)
        super().__init__(**kwargs)

    def to_representation(self, value):
        if not value:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(value.url) if request else value.url
