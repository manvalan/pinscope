"""Content-addressed datasheet storage for the shared library.

Stores PDF blobs by their MD5 hash and creates lightweight JSON ref files
that map MPN names to blob keys.  This deduplicates identical PDFs that
were previously stored under different human-readable names.

Layout::

    library/datasheets/
      blobs/{md5hash}.pdf          -- unique PDF content, stored once
      refs/{safe_mpn}.json         -- per-MPN pointer: {"hash": "...", "blob_key": "..."}
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from backend.pinscopex.utils import safe_mpn
from backend.services.storage import StorageBackend

BLOB_PREFIX = "library/datasheets/blobs/"
REF_PREFIX = "library/datasheets/refs/"
ALIAS_KEY = "library/datasheets/aliases.json"


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def compute_md5_from_path(local_path: Path) -> str:
    """Return the hex MD5 digest of a local file (chunked read)."""
    h = hashlib.md5()
    with open(local_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_md5_from_bytes(data: bytes) -> str:
    """Return the hex MD5 digest of in-memory bytes."""
    return hashlib.md5(data).hexdigest()


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------

def blob_key(md5: str) -> str:
    """Storage key for a content-addressed PDF blob."""
    return f"{BLOB_PREFIX}{md5}.pdf"


def ref_key(mpn: str) -> str:
    """Storage key for an MPN → blob ref file."""
    return f"{REF_PREFIX}{safe_mpn(mpn)}.json"


# ---------------------------------------------------------------------------
# Store / resolve / delete
# ---------------------------------------------------------------------------

def store_datasheet(
    storage: StorageBackend,
    local_path: Path,
    mpn: str,
) -> str:
    """Store a datasheet PDF by content hash and create an MPN ref.

    Idempotent: skips blob upload if it already exists, always writes the ref.
    Returns the blob storage key.
    """
    md5 = compute_md5_from_path(local_path)
    bk = blob_key(md5)
    if not storage.exists(bk):
        storage.upload_from_local(local_path, bk)
    storage.write_json(ref_key(mpn), {"hash": md5, "blob_key": bk, "mpn": mpn})
    return bk


def store_datasheet_bytes(
    storage: StorageBackend,
    data: bytes,
    mpn: str,
    extra_mpns: list[str] | None = None,
) -> str:
    """Same as :func:`store_datasheet` but from in-memory bytes.

    ``extra_mpns`` are additional catalog/orderable codes that should point
    at the same blob (family MPN vs ``…-N16R16V``).
    """
    md5 = compute_md5_from_bytes(data)
    bk = blob_key(md5)
    if not storage.exists(bk):
        storage.write_bytes(bk, data)
    names = [mpn, *(extra_mpns or [])]
    seen: set[str] = set()
    for name in names:
        name = (name or "").strip()
        if not name:
            continue
        key = name.upper()
        if key in seen:
            continue
        seen.add(key)
        storage.write_json(ref_key(name), {"hash": md5, "blob_key": bk, "mpn": name})
    _record_aliases(storage, mpn, extra_mpns or [])
    return bk


def _record_aliases(storage: StorageBackend, mpn: str, extra_mpns: list[str]) -> None:
    from backend.services.datasheet_finder import _alnum, _MIN_FAMILY_LEN

    names = [mpn, *extra_mpns]
    compact = {n: _alnum(n) for n in names if n and n.strip()}
    if len(set(compact.values())) < 2 and not extra_mpns:
        return
    table: dict[str, str] = {}
    if storage.exists(ALIAS_KEY):
        raw = storage.read_json(ALIAS_KEY)
        table = dict(raw.get("aliases") or {})
    canonical = extra_mpns[0].strip() if extra_mpns else mpn
    for name, key in compact.items():
        if len(key) >= _MIN_FAMILY_LEN:
            table[key] = canonical
    storage.write_json(ALIAS_KEY, {"aliases": table})


def resolve_datasheet(storage: StorageBackend, mpn: str) -> str | None:
    """Look up the blob key for an MPN via its ref file.

    Tries spelling variants, then the shared alias table (family MPN →
    orderable code stored in the library).
    """
    from backend.services.datasheet_finder import (
        _MIN_FAMILY_LEN,
        _alnum,
        mpn_query_variants,
    )

    def _from_ref(name: str) -> str | None:
        rk = ref_key(name)
        if not storage.exists(rk):
            return None
        ref = storage.read_json(rk)
        bk = ref.get("blob_key")
        if bk and storage.exists(bk):
            return bk
        return None

    for name in mpn_query_variants(mpn) or [mpn]:
        hit = _from_ref(name)
        if hit:
            return hit

    if not storage.exists(ALIAS_KEY):
        return None
    table = (storage.read_json(ALIAS_KEY) or {}).get("aliases") or {}
    want = _alnum(mpn)
    if not want:
        return None
    target = table.get(want)
    if target:
        hit = _from_ref(target)
        if hit:
            return hit
    if len(want) >= _MIN_FAMILY_LEN:
        for key, target in table.items():
            if key.startswith(want) or (
                want.startswith(key) and len(key) >= _MIN_FAMILY_LEN
            ):
                hit = _from_ref(target)
                if hit:
                    return hit
    return None


def delete_datasheet_ref(storage: StorageBackend, mpn: str) -> str | None:
    """Delete the ref for an MPN.  Returns the blob key if a ref existed.

    Does **not** delete the blob — other refs may point to it.  Use
    :func:`gc_orphan_blobs` to clean up unreferenced blobs.
    """
    rk = ref_key(mpn)
    if not storage.exists(rk):
        return None
    ref = storage.read_json(rk)
    bk = ref.get("blob_key")
    storage.delete_key(rk)
    return bk


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def gc_orphan_blobs(
    storage: StorageBackend, *, dry_run: bool = True,
) -> list[str]:
    """Find blobs not referenced by any ref file.  Optionally delete them.

    Also checks pattern ``datasheet_key`` values so blobs referenced only
    by patterns (not MPN refs) are kept.

    Intended for maintenance scripts, not hot paths.
    """
    # Collect all hashes referenced by ref files
    referenced_hashes: set[str] = set()
    for rk in storage.list_recursive(REF_PREFIX):
        if rk.endswith(".json"):
            ref = storage.read_json(rk)
            h = ref.get("hash")
            if h:
                referenced_hashes.add(h)

    # Also collect hashes from pattern datasheet_key values
    for pk in storage.list_recursive("library/patterns/"):
        if pk.endswith(".json"):
            pat = storage.read_json(pk)
            ds_key = pat.get("datasheet_key", "")
            if ds_key.startswith(BLOB_PREFIX) and ds_key.endswith(".pdf"):
                h = ds_key.removeprefix(BLOB_PREFIX).removesuffix(".pdf")
                referenced_hashes.add(h)

    # Find orphan blobs
    orphans: list[str] = []
    for bk in storage.list_recursive(BLOB_PREFIX):
        if not bk.endswith(".pdf"):
            continue
        filename = bk.rsplit("/", 1)[-1]
        h = filename.removesuffix(".pdf")
        if h not in referenced_hashes:
            orphans.append(bk)
            if not dry_run:
                storage.delete_key(bk)

    return orphans
