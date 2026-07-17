"""Storage abstraction layer.

Provides a StorageBackend protocol with two implementations:
  - LocalStorageBackend: maps GCS-style keys to local filesystem paths (dev/test)
  - GCSStorageBackend: uses Google Cloud Storage (production)

Keys use forward-slash-separated paths like GCS object names:
  users/{user_id}/projects/{project_id}/project.json
  library/extracted/{safe_mpn}.json
  taxonomy/ic.json
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable


# Sentinel used by conditional writes to require that the object does not yet
# exist (matches GCS ``if_generation_match=0`` semantics).
GENERATION_NEW = 0


class StaleGeneration(Exception):
    """Raised when a conditional write loses an optimistic-concurrency race."""


@runtime_checkable
class StorageBackend(Protocol):
    """Abstract storage interface used by all backend services."""

    def read_json(self, key: str) -> dict:
        """Read and parse a JSON object."""
        ...

    def write_json(self, key: str, data: dict) -> None:
        """Serialize and write a JSON object."""
        ...

    def read_bytes(self, key: str) -> bytes:
        """Read raw bytes."""
        ...

    def write_bytes(self, key: str, data: bytes) -> None:
        """Write raw bytes."""
        ...

    def read_text(self, key: str) -> str:
        """Read as UTF-8 text."""
        ...

    def write_text(self, key: str, text: str) -> None:
        """Write UTF-8 text."""
        ...

    def exists(self, key: str) -> bool:
        """Check if an object exists."""
        ...

    def list_prefix(self, prefix: str) -> list[str]:
        """List all keys under a prefix (non-recursive by default).

        Returns keys that are direct children of the prefix — i.e. one level
        deep. For example, listing ``users/abc/projects/`` returns keys like
        ``users/abc/projects/p1/project.json`` but NOT keys nested further.

        To list all keys recursively, use list_recursive().
        """
        ...

    def list_recursive(self, prefix: str) -> list[str]:
        """List all keys under a prefix, recursively."""
        ...

    def list_prefix_after(self, prefix: str, after_key: str | None = None) -> list[str]:
        """List keys under ``prefix`` whose name lexicographically follows
        ``after_key``. Used by the GCS-backed event tail (worker writes one
        object per event with a zero-padded sequence number; the SSE
        consumer pages through new files only).
        """
        ...

    def read_json_with_generation(self, key: str) -> tuple[dict, int]:
        """Read JSON and return ``(data, generation)``.

        ``generation`` is an opaque token that callers pass back to
        ``write_json_if_match`` to detect lost-update races.
        """
        ...

    def write_json_if_match(self, key: str, data: dict, generation: int) -> int:
        """Write JSON only if the current generation equals ``generation``.

        Pass ``GENERATION_NEW`` (0) to require that the key does not exist.
        Returns the new generation. Raises :class:`StaleGeneration` when the
        precondition fails (loser of a race).
        """
        ...

    def delete_key(self, key: str) -> None:
        """Delete a single object."""
        ...

    def delete_prefix(self, prefix: str) -> None:
        """Delete all objects under a prefix (recursive)."""
        ...

    def copy_object(self, src_key: str, dst_key: str) -> None:
        """Copy an object from src to dst."""
        ...

    def download_to_local(self, key: str, local_path: Path) -> Path:
        """Download an object to a local file path. Returns the local path."""
        ...

    def upload_from_local(self, local_path: Path, key: str) -> None:
        """Upload a local file to storage."""
        ...

    def signed_url(self, key: str, expiration_minutes: int = 15) -> str:
        """Generate a time-limited URL for direct access to an object.

        For LocalStorageBackend, returns a backend-proxied URL.
        For GCS, returns a signed GCS URL.
        """
        ...


class LocalStorageBackend:
    """Maps GCS-style keys to local filesystem paths under a base directory.

    Key ``users/abc/projects/p1/project.json`` becomes
    ``{base_dir}/users/abc/projects/p1/project.json``.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        # In-memory generation counter for optimistic-concurrency parity with
        # GCS. Single-process only; subprocess-based local workers run in a
        # different process and will collide on the meta key. The local
        # subprocess path is dev-only and rarely concurrent, so we accept it.
        self._generations: dict[str, int] = {}
        self._gen_lock = threading.Lock()

    def _path(self, key: str) -> Path:
        return self._base / key

    def read_json(self, key: str) -> dict:
        return json.loads(self._path(key).read_text())

    def write_json(self, key: str, data: dict) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2) + "\n")

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def read_text(self, key: str) -> str:
        return self._path(key).read_text()

    def write_text(self, key: str, text: str) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_prefix(self, prefix: str) -> list[str]:
        d = self._path(prefix)
        if not d.is_dir():
            return []
        keys: list[str] = []
        for child in sorted(d.iterdir()):
            rel = child.relative_to(self._base)
            keys.append(str(rel))
        return keys

    def list_recursive(self, prefix: str) -> list[str]:
        d = self._path(prefix)
        if not d.is_dir():
            return []
        keys: list[str] = []
        for child in sorted(d.rglob("*")):
            if child.is_file():
                rel = child.relative_to(self._base)
                keys.append(str(rel))
        return keys

    def list_prefix_after(self, prefix: str, after_key: str | None = None) -> list[str]:
        d = self._path(prefix)
        if not d.is_dir():
            return []
        keys: list[str] = []
        for child in sorted(d.iterdir()):
            if not child.is_file():
                continue
            rel = str(child.relative_to(self._base))
            if after_key is not None and rel <= after_key:
                continue
            keys.append(rel)
        return keys

    def read_json_with_generation(self, key: str) -> tuple[dict, int]:
        data = json.loads(self._path(key).read_text())
        with self._gen_lock:
            gen = self._generations.get(key, 1)
        return data, gen

    def write_json_if_match(self, key: str, data: dict, generation: int) -> int:
        p = self._path(key)
        with self._gen_lock:
            current = self._generations.get(key, 0 if not p.is_file() else 1)
            if generation != current:
                raise StaleGeneration(
                    f"generation mismatch on {key}: expected {generation}, current {current}"
                )
            new_gen = current + 1
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2) + "\n")
            self._generations[key] = new_gen
        return new_gen

    def delete_key(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            p.unlink()
            with self._gen_lock:
                self._generations.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:
        d = self._path(prefix)
        if d.is_dir():
            shutil.rmtree(d)

    def copy_object(self, src_key: str, dst_key: str) -> None:
        src = self._path(src_key)
        dst = self._path(dst_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    def download_to_local(self, key: str, local_path: Path) -> Path:
        src = self._path(key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, local_path)
        return local_path

    def upload_from_local(self, local_path: Path, key: str) -> None:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dst)

    def signed_url(self, key: str, expiration_minutes: int = 15) -> str:
        # Local dev: return a path that the backend can serve directly
        return f"/api/datasheets/_local/{key}"
