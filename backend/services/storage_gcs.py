"""Google Cloud Storage backend for StorageBackend protocol."""

from __future__ import annotations

import json
from pathlib import Path

from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage as gcs

from backend.services.storage import StaleGeneration


class GCSStorageBackend:
    """StorageBackend implementation using Google Cloud Storage."""

    def __init__(self, bucket_name: str) -> None:
        self._client = gcs.Client()
        self._bucket = self._client.bucket(bucket_name)

    def _blob(self, key: str) -> gcs.Blob:
        return self._bucket.blob(key)

    def read_json(self, key: str) -> dict:
        text = self._blob(key).download_as_text()
        return json.loads(text)

    def write_json(self, key: str, data: dict) -> None:
        text = json.dumps(data, indent=2) + "\n"
        self._blob(key).upload_from_string(text, content_type="application/json")

    def read_bytes(self, key: str) -> bytes:
        return self._blob(key).download_as_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        self._blob(key).upload_from_string(data)

    def read_text(self, key: str) -> str:
        return self._blob(key).download_as_text()

    def write_text(self, key: str, text: str) -> None:
        self._blob(key).upload_from_string(text, content_type="text/plain")

    def exists(self, key: str) -> bool:
        return self._blob(key).exists()

    def list_prefix(self, prefix: str) -> list[str]:
        # List immediate children (one level) using delimiter
        blobs = self._client.list_blobs(
            self._bucket, prefix=prefix, delimiter="/",
        )
        keys: list[str] = []
        # Files directly under prefix
        for blob in blobs:
            keys.append(blob.name)
        # "Subdirectories" — strip trailing slash for consistency
        for pfx in blobs.prefixes:
            keys.append(pfx.rstrip("/"))
        return sorted(keys)

    def list_recursive(self, prefix: str) -> list[str]:
        blobs = self._client.list_blobs(self._bucket, prefix=prefix)
        return sorted(blob.name for blob in blobs)

    def list_prefix_after(self, prefix: str, after_key: str | None = None) -> list[str]:
        # Use GCS ``start_offset`` to skip already-seen keys server-side. We
        # ask for the next-after value; since after_key may be the last seen
        # key, advance one byte so the listing excludes it.
        kwargs: dict = {"prefix": prefix, "delimiter": "/"}
        if after_key is not None:
            # Request keys strictly greater than after_key. Append a NUL byte
            # so GCS treats start_offset as "after" rather than "starting at".
            kwargs["start_offset"] = after_key + "\x00"
        blobs = self._client.list_blobs(self._bucket, **kwargs)
        return sorted(blob.name for blob in blobs)

    def read_json_with_generation(self, key: str) -> tuple[dict, int]:
        blob = self._blob(key)
        text = blob.download_as_text()
        # download_as_text populates blob.generation as a side effect.
        gen = int(blob.generation) if blob.generation is not None else 0
        return json.loads(text), gen

    def write_json_if_match(self, key: str, data: dict, generation: int) -> int:
        text = json.dumps(data, indent=2) + "\n"
        blob = self._blob(key)
        try:
            blob.upload_from_string(
                text,
                content_type="application/json",
                if_generation_match=generation,
            )
        except PreconditionFailed as exc:
            raise StaleGeneration(
                f"generation mismatch on {key}: expected {generation}"
            ) from exc
        # blob.generation is set by upload_from_string on success.
        return int(blob.generation) if blob.generation is not None else 0

    def delete_key(self, key: str) -> None:
        blob = self._blob(key)
        if blob.exists():
            blob.delete()

    def delete_prefix(self, prefix: str) -> None:
        blobs = list(self._client.list_blobs(self._bucket, prefix=prefix))
        if blobs:
            self._bucket.delete_blobs(blobs)

    def copy_object(self, src_key: str, dst_key: str) -> None:
        src_blob = self._blob(src_key)
        self._bucket.copy_blob(src_blob, self._bucket, dst_key)

    def download_to_local(self, key: str, local_path: Path) -> Path:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._blob(key).download_to_filename(str(local_path))
        return local_path

    def upload_from_local(self, local_path: Path, key: str) -> None:
        self._blob(key).upload_from_filename(str(local_path))

    def signed_url(self, key: str, expiration_minutes: int = 15) -> str:
        # Not used for GCS on Cloud Run — the backend proxies PDFs directly
        # via the /datasheet-proxy/ endpoint instead.  Kept for interface
        # compatibility.
        raise NotImplementedError("Use read_bytes() and proxy instead")
