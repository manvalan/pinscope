#!/usr/bin/env python3
"""Remove duplicate per-MPN datasheet PDFs from library/datasheets/.

Loads all passive patterns and checks each library/datasheets/{name}.pdf to see
if a pattern covers that MPN and has a datasheet_key pointing to a different file.
If so, the per-MPN copy is redundant and can be deleted.

Works with both LocalStorageBackend and GCSStorageBackend depending on
whether GCS_BUCKET is set.

Usage:
    # Dry run (default) — shows what would be deleted
    python -m scripts.dedup_library_datasheets

    # Actually delete
    python -m scripts.dedup_library_datasheets --apply
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
    parser = argparse.ArgumentParser(description="Remove duplicate per-MPN datasheets from library")
    parser.add_argument("--apply", action="store_true", help="Actually delete files (default is dry run)")
    args = parser.parse_args()

    storage = get_storage()
    print(f"Storage backend: {type(storage).__name__}")

    # Load all passive patterns
    from backend.services.projects import load_library_patterns
    patterns = load_library_patterns(storage)
    print(f"Loaded {len(patterns)} passive pattern(s)")

    if not patterns:
        print("No patterns found — nothing to deduplicate")
        return

    from backend.pinscopex.resolve_passives import resolve_mpn

    # List all library datasheet PDFs
    all_ds_keys = storage.list_recursive("library/datasheets/")
    pdf_keys = [k for k in all_ds_keys if k.endswith(".pdf")]
    print(f"Found {len(pdf_keys)} library datasheet PDF(s)")

    # Collect canonical keys (pattern datasheet_keys) so we never delete them
    canonical_keys = set()
    for pat in patterns:
        if pat.datasheet_key:
            canonical_keys.add(pat.datasheet_key)

    redundant = 0
    kept = 0

    for key in pdf_keys:
        # Never delete a canonical pattern datasheet
        if key in canonical_keys:
            kept += 1
            continue

        # Extract MPN from filename: library/datasheets/{safe_mpn}.pdf
        filename = key.rsplit("/", 1)[-1]
        mpn = filename.removesuffix(".pdf")

        # Check if a pattern covers this MPN
        match = resolve_mpn(mpn, patterns)
        if match is None:
            # Not a passive, or no pattern covers it — keep it
            kept += 1
            continue

        pat = match[0]
        if not pat.datasheet_key:
            # Pattern has no datasheet_key — keep the per-MPN copy
            kept += 1
            continue

        # Pattern's canonical datasheet covers this MPN — per-MPN copy is redundant
        if args.apply:
            storage.delete_key(key)
            print(f"  DELETE  {key}  (covered by {pat.datasheet_key})")
        else:
            print(f"  WOULD DELETE  {key}  (covered by {pat.datasheet_key})")
        redundant += 1

    print()
    if args.apply:
        print(f"Done: {redundant} deleted, {kept} kept")
    else:
        print(f"Dry run: {redundant} would be deleted, {kept} kept")
        if redundant > 0:
            print("Run with --apply to execute")


if __name__ == "__main__":
    main()
