#!/usr/bin/env python3
"""Migrate library datasheets to content-addressed blob storage.

Converts flat ``library/datasheets/{name}.pdf`` files into the new layout::

    library/datasheets/blobs/{md5}.pdf   -- unique content
    library/datasheets/refs/{name}.json  -- MPN pointer

Also updates ``datasheet_key`` in pattern JSON files to point to the new
blob paths.

Works with both LocalStorageBackend and GCSStorageBackend depending on
whether GCS_BUCKET is set.

Usage:
    # Dry run (default) -- shows what would happen
    python -m scripts.migrate_datasheets_to_blobs

    # Create blobs + refs + update patterns
    python -m scripts.migrate_datasheets_to_blobs --apply

    # After verifying, delete original flat files
    python -m scripts.migrate_datasheets_to_blobs --apply --cleanup
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from backend.services.datasheet_store import (
    BLOB_PREFIX,
    REF_PREFIX,
    blob_key,
    compute_md5_from_path,
)


def get_storage():
    from backend.config import settings

    if settings.gcs_bucket:
        from backend.services.storage_gcs import GCSStorageBackend
        return GCSStorageBackend(settings.gcs_bucket)
    else:
        from backend.services.storage import LocalStorageBackend
        return LocalStorageBackend(settings.data_dir)


def main():
    parser = argparse.ArgumentParser(
        description="Migrate library datasheets to content-addressed blob storage",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually create blobs/refs and update patterns (default is dry run)",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Delete original flat PDF files (only with --apply, after verification)",
    )
    args = parser.parse_args()

    if args.cleanup and not args.apply:
        parser.error("--cleanup requires --apply")

    storage = get_storage()
    print(f"Storage backend: {type(storage).__name__}")

    # List all flat library datasheet PDFs (exclude blobs/ and refs/ subdirs)
    all_keys = storage.list_recursive("library/datasheets/")
    flat_pdfs = [
        k for k in all_keys
        if k.endswith(".pdf")
        and not k.startswith(BLOB_PREFIX)
        and not k.startswith(REF_PREFIX)
    ]
    print(f"Found {len(flat_pdfs)} flat library datasheet PDF(s)")

    if not flat_pdfs:
        print("Nothing to migrate")
        return

    # Phase 1: Create blobs and refs for each flat PDF
    blobs_created = 0
    blobs_skipped = 0
    refs_created = 0
    hash_map: dict[str, str] = {}  # old key -> md5 hash

    with tempfile.TemporaryDirectory() as tmpdir:
        for key in flat_pdfs:
            filename = key.rsplit("/", 1)[-1]
            name = filename.removesuffix(".pdf")

            # Download to temp
            local_path = Path(tmpdir) / filename
            storage.download_to_local(key, local_path)
            md5 = compute_md5_from_path(local_path)
            hash_map[key] = md5

            bk = blob_key(md5)
            rk = f"{REF_PREFIX}{name}.json"

            if args.apply:
                if not storage.exists(bk):
                    storage.upload_from_local(local_path, bk)
                    blobs_created += 1
                else:
                    blobs_skipped += 1
                storage.write_json(rk, {"hash": md5, "blob_key": bk})
                refs_created += 1
            else:
                existing = storage.exists(bk)
                if existing:
                    blobs_skipped += 1
                    print(f"  BLOB EXISTS  {bk}  (hash {md5})")
                else:
                    blobs_created += 1
                    print(f"  WOULD CREATE BLOB  {bk}")
                refs_created += 1
                print(f"  WOULD CREATE REF   {rk}  -> {md5}")

    print(f"\nBlobs: {blobs_created} created, {blobs_skipped} already existed")
    print(f"Refs:  {refs_created} created")

    # Deduplicate report
    unique_hashes = set(hash_map.values())
    if len(flat_pdfs) > len(unique_hashes):
        saved = len(flat_pdfs) - len(unique_hashes)
        print(f"Deduplication: {len(flat_pdfs)} files -> {len(unique_hashes)} unique blobs ({saved} duplicates)")

    # Phase 2: Update pattern datasheet_key values
    pattern_keys = storage.list_recursive("library/patterns/")
    pattern_jsons = [k for k in pattern_keys if k.endswith(".json")]
    patterns_updated = 0

    for pk in pattern_jsons:
        pat = storage.read_json(pk)
        ds_key = pat.get("datasheet_key", "")

        # Only update if it matches old flat format
        if not ds_key or ds_key.startswith(BLOB_PREFIX):
            continue
        if not ds_key.startswith("library/datasheets/") or not ds_key.endswith(".pdf"):
            continue

        md5 = hash_map.get(ds_key)
        if md5 is None:
            # The pattern references a flat file we didn't find -- skip
            print(f"  WARNING  Pattern {pk} references missing datasheet: {ds_key}")
            continue

        new_ds_key = blob_key(md5)

        if args.apply:
            pat["datasheet_key"] = new_ds_key
            storage.write_json(pk, pat)
            patterns_updated += 1
            print(f"  UPDATED  {pk}  datasheet_key -> {new_ds_key}")
        else:
            patterns_updated += 1
            print(f"  WOULD UPDATE  {pk}  datasheet_key: {ds_key} -> {new_ds_key}")

    print(f"\nPatterns updated: {patterns_updated}")

    # Phase 3: Verify (when applying)
    if args.apply:
        print("\n--- Verification ---")
        errors = 0

        # Every ref should point to an existing blob
        all_refs = storage.list_recursive(REF_PREFIX)
        for rk in all_refs:
            if not rk.endswith(".json"):
                continue
            ref = storage.read_json(rk)
            bk = ref.get("blob_key")
            if not bk or not storage.exists(bk):
                print(f"  ERROR  Ref {rk} points to missing blob: {bk}")
                errors += 1

        # Every pattern datasheet_key should resolve
        for pk in pattern_jsons:
            pat = storage.read_json(pk)
            ds_key = pat.get("datasheet_key", "")
            if ds_key and ds_key.startswith(BLOB_PREFIX) and not storage.exists(ds_key):
                print(f"  ERROR  Pattern {pk} references missing blob: {ds_key}")
                errors += 1

        if errors:
            print(f"\n{errors} error(s) found -- do NOT run --cleanup until resolved")
        else:
            print("All refs and patterns verified OK")

    # Phase 4: Cleanup old flat files
    if args.cleanup:
        print("\n--- Cleanup ---")
        deleted = 0
        for key in flat_pdfs:
            storage.delete_key(key)
            deleted += 1
            print(f"  DELETE  {key}")
        print(f"\nDeleted {deleted} flat file(s)")
    elif args.apply:
        print(f"\nFlat files preserved. Run with --apply --cleanup to delete {len(flat_pdfs)} original file(s)")
    else:
        print(f"\nDry run complete. Run with --apply to execute.")


if __name__ == "__main__":
    main()
