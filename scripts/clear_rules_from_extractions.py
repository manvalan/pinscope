#!/usr/bin/env python3
"""Clear the deprecated `rules` field from all extracted component JSON files in GCS.

Since the validation pipeline now reads datasheets directly instead of relying on
pre-extracted rules, the `rules` list in every ComponentConstraints JSON is stale
and should be emptied.

Scans:
  library/extracted/*.json           — shared library IC extractions
  users/*/projects/*/extracted/*.json — per-project IC extractions

Sets `rules` to [] on any file where it is non-empty.

Works with both LocalStorageBackend and GCSStorageBackend depending on
whether GCS_BUCKET is set.

Usage:
    # Dry run (default) — shows what would be changed
    python -m scripts.clear_rules_from_extractions

    # Actually apply changes
    python -m scripts.clear_rules_from_extractions --apply
"""

from __future__ import annotations

import argparse
import json


def get_storage():
    from backend.config import settings

    if settings.gcs_bucket:
        from backend.services.storage_gcs import GCSStorageBackend
        return GCSStorageBackend(settings.gcs_bucket)
    else:
        from backend.services.storage import LocalStorageBackend
        return LocalStorageBackend(settings.data_dir)


def find_extraction_keys(storage) -> list[str]:
    """Return all extracted component JSON keys across library and per-project paths."""
    keys: list[str] = []

    # Shared library extractions
    for key in storage.list_recursive("library/extracted/"):
        if key.endswith(".json"):
            keys.append(key)

    # Per-project extractions: users/{uid}/projects/{pid}/extracted/*.json
    # We can't know all user IDs in advance, so list from the top.
    try:
        user_prefixes = [
            k for k in storage.list_prefix("users/")
            if not k.endswith(".json")
        ]
    except Exception:
        user_prefixes = []

    for user_prefix in user_prefixes:
        projects_prefix = user_prefix.rstrip("/") + "/projects/"
        try:
            project_prefixes = [
                k for k in storage.list_prefix(projects_prefix)
                if not k.endswith(".json")
            ]
        except Exception:
            continue

        for project_prefix in project_prefixes:
            extracted_prefix = project_prefix.rstrip("/") + "/extracted/"
            for key in storage.list_recursive(extracted_prefix):
                if key.endswith(".json"):
                    keys.append(key)

    return sorted(set(keys))


def main():
    parser = argparse.ArgumentParser(
        description="Clear deprecated 'rules' field from extracted component JSON files"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes (default is dry run)",
    )
    args = parser.parse_args()

    storage = get_storage()
    print(f"Storage backend: {type(storage).__name__}")

    keys = find_extraction_keys(storage)
    print(f"Found {len(keys)} extracted JSON file(s) to inspect\n")

    changed = 0
    skipped = 0
    errors = 0

    for key in keys:
        try:
            data = storage.read_json(key)
        except Exception as exc:
            print(f"  ERROR reading {key}: {exc}")
            errors += 1
            continue

        rules = data.get("rules")
        if not rules:
            # Already empty or missing — nothing to do
            skipped += 1
            continue

        rule_count = len(rules)
        data["rules"] = []

        if args.apply:
            try:
                storage.write_json(key, data)
                print(f"  CLEARED  {key}  ({rule_count} rule(s) removed)")
            except Exception as exc:
                print(f"  ERROR writing {key}: {exc}")
                errors += 1
                continue
        else:
            print(f"  WOULD CLEAR  {key}  ({rule_count} rule(s))")

        changed += 1

    print()
    if args.apply:
        print(
            f"Done: {changed} file(s) updated, {skipped} already empty, {errors} error(s)"
        )
    else:
        print(
            f"Dry run: {changed} file(s) would be updated, {skipped} already empty, {errors} error(s)"
        )
        if changed > 0:
            print("Run with --apply to execute")


if __name__ == "__main__":
    main()
