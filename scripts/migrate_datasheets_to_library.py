#!/usr/bin/env python3
"""One-time migration: copy per-project datasheets into library/datasheets/.

Scans all users/*/projects/*/uploads/datasheets/*.pdf and copies each to
library/datasheets/{filename} if it doesn't already exist there.

Works with both LocalStorageBackend and GCSStorageBackend depending on
whether GCS_BUCKET is set.

Usage:
    # Dry run (default) — shows what would be copied
    python -m scripts.migrate_datasheets_to_library

    # Actually copy
    python -m scripts.migrate_datasheets_to_library --apply
"""

from __future__ import annotations

import argparse
import sys


def get_storage():
    from backend.config import settings

    if settings.gcs_bucket:
        from backend.services.storage_gcs import GCSStorageBackend
        return GCSStorageBackend(settings.gcs_bucket)
    else:
        from backend.services.storage import LocalStorageBackend
        return LocalStorageBackend(settings.data_dir)


def main():
    parser = argparse.ArgumentParser(description="Migrate per-project datasheets to library/datasheets/")
    parser.add_argument("--apply", action="store_true", help="Actually copy files (default is dry run)")
    args = parser.parse_args()

    storage = get_storage()
    print(f"Storage backend: {type(storage).__name__}")

    from backend.services.datasheet_store import resolve_datasheet, store_datasheet_bytes

    # Find all per-project datasheet PDFs
    all_keys = storage.list_recursive("users/")
    datasheet_keys = [k for k in all_keys if "/uploads/datasheets/" in k and k.endswith(".pdf")]

    print(f"Found {len(datasheet_keys)} per-project datasheet(s)")

    copied = 0
    skipped = 0

    for key in datasheet_keys:
        filename = key.rsplit("/", 1)[-1]
        mpn = filename.removesuffix(".pdf")

        # Skip if already in library (ref-based or legacy flat file)
        if resolve_datasheet(storage, mpn) is not None:
            print(f"  SKIP  {filename} (already in library)")
            skipped += 1
            continue
        lib_key = f"library/datasheets/{filename}"
        if storage.exists(lib_key):
            print(f"  SKIP  {filename} (legacy flat file exists)")
            skipped += 1
            continue

        if args.apply:
            data = storage.read_bytes(key)
            bk = store_datasheet_bytes(storage, data, mpn)
            print(f"  STORE  {key} -> {bk}")
            copied += 1
        else:
            print(f"  WOULD STORE  {key} -> blob + ref")
            copied += 1

    print()
    if args.apply:
        print(f"Done: {copied} stored, {skipped} skipped")
    else:
        print(f"Dry run: {copied} would be stored, {skipped} already in library")
        if copied > 0:
            print("Run with --apply to execute")


if __name__ == "__main__":
    main()
