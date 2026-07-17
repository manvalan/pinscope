#!/usr/bin/env python3
"""Remove orphan datasheet blobs not referenced by any ref or pattern.

Works with both LocalStorageBackend and GCSStorageBackend depending on
whether GCS_BUCKET is set.

Usage:
    # Dry run (default) -- shows what would be deleted
    python -m scripts.gc_orphan_blobs

    # Actually delete
    python -m scripts.gc_orphan_blobs --apply
"""

from __future__ import annotations

import argparse


def get_storage():
    from backend.config import settings

    if settings.gcs_bucket:
        from backend.services.storage_gcs import GCSStorageBackend
        return GCSStorageBackend(settings.gcs_bucket)
    else:
        from backend.services.storage import LocalStorageBackend
        return LocalStorageBackend(settings.data_dir)


def main():
    parser = argparse.ArgumentParser(description="Remove orphan datasheet blobs")
    parser.add_argument("--apply", action="store_true", help="Actually delete (default is dry run)")
    args = parser.parse_args()

    storage = get_storage()
    print(f"Storage backend: {type(storage).__name__}")

    from backend.services.datasheet_store import gc_orphan_blobs

    orphans = gc_orphan_blobs(storage, dry_run=not args.apply)

    if not orphans:
        print("No orphan blobs found")
        return

    for bk in orphans:
        prefix = "  DELETE" if args.apply else "  WOULD DELETE"
        print(f"{prefix}  {bk}")

    print()
    if args.apply:
        print(f"Deleted {len(orphans)} orphan blob(s)")
    else:
        print(f"Dry run: {len(orphans)} orphan blob(s) would be deleted")
        print("Run with --apply to execute")


if __name__ == "__main__":
    main()
