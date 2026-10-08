"""Media storage on Vercel Blob, for deployments where the local filesystem is read-only and ephemeral."""

import mimetypes
import os
from urllib.parse import quote

from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible

import requests
from vercel import blob


@deconstructible
class VercelBlobStorage(Storage):
    """Public-access Blob store, authenticated by ``BLOB_READ_WRITE_TOKEN`` (set when a store is connected).

    Files keep the exact name Django picks (no random suffix), so ``url()`` is built locally from the
    store id embedded in the token instead of costing a request per image.
    """

    def __init__(self, token: str | None = None):
        self._token = token

    @property
    def token(self) -> str:
        token = self._token or os.environ.get("BLOB_READ_WRITE_TOKEN", "")
        if not token:
            raise ImproperlyConfigured("BLOB_READ_WRITE_TOKEN is required for VercelBlobStorage.")
        return token

    @property
    def base_url(self) -> str:
        # Tokens look like vercel_blob_rw_<storeId>_<secret>.
        store_id = self.token.split("_")[3].lower()
        return f"https://{store_id}.public.blob.vercel-storage.com/"

    @staticmethod
    def _clean(name: str) -> str:
        # Django builds alternative names with os.path: backslashes when run on Windows (e.g. `seed`).
        return name.replace("\\", "/")

    def _save(self, name, content):
        name = self._clean(name)
        content.seek(0)
        content_type = getattr(content, "content_type", None) or mimetypes.guess_type(name)[0]
        result = blob.put(
            name,
            content.read(),
            access="public",
            content_type=content_type,
            add_random_suffix=False,
            overwrite=True,
            token=self.token,
        )
        return result.pathname

    def _open(self, name, mode="rb"):
        response = requests.get(self.url(name), timeout=30)
        response.raise_for_status()
        return ContentFile(response.content, name=name)

    def exists(self, name):
        try:
            blob.head(self._clean(name), token=self.token)
        except blob.BlobNotFoundError:
            return False
        return True

    def delete(self, name):
        if name:
            blob.delete(self.url(name), token=self.token)

    def size(self, name):
        return blob.head(self._clean(name), token=self.token).size

    def url(self, name):
        return self.base_url + quote(self._clean(name))
