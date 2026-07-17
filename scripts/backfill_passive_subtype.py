#!/usr/bin/env python3
"""Backfill `component_subtype` on typed passive spec files + existing BOM summaries.

Until this fix, typed `ResistorSpecs`/`CapacitorSpecs`/`InductorSpecs` had no
`component_subtype` field, so passives resolved via DigiKey, LCSC, or the
Haiku value-fallback were persisted without a taxonomy classification. The
BOM tab's Category column reads `BomSummaryRow.category` (collated from the
design graph at pipeline time), so those passives showed "—".

This script does two passes:

  1. Patch model files (`library/passives/*.json` + `users/*/projects/*/models/*.json`)
     — on typed passive specs missing `component_subtype`, set a coarse value
     derived from `specs_type` ("capacitor" → "passive.capacitor", etc.).

  2. Patch existing `users/*/projects/*/bom_summary.json` in place — for any
     row whose `category` is null/empty AND whose mpn matches a patched model,
     set the category. This is what surfaces in the BOM tab without needing
     to rerun the pipeline.

Refined subtype (e.g. "passive.capacitor.ceramic" vs ".tantalum") requires
the original DigiKey/LCSC payload and is intentionally out of scope — future
pipeline runs will produce the refined value.

Derating output is unchanged: `_dielectric_category` only refines on
substrings "ceramic"/"tantalum"/"electrolytic", which the coarse fallback
doesn't contain.

Usage:
    python -m scripts.backfill_passive_subtype           # dry run
    python -m scripts.backfill_passive_subtype --apply   # write changes
"""

from __future__ import annotations

import argparse


SPECS_TYPE_TO_SUBTYPE = {
    "resistor": "passive.resistor",
    "capacitor": "passive.capacitor",
    "inductor": "passive.inductor",
}


def get_storage():
    from backend.config import settings

    if settings.gcs_bucket:
        from backend.services.storage_gcs import GCSStorageBackend
        return GCSStorageBackend(settings.gcs_bucket)
    else:
        from backend.services.storage import LocalStorageBackend
        return LocalStorageBackend(settings.data_dir)


def list_project_prefixes(storage) -> list[str]:
    """Return all `users/{uid}/projects/{pid}/` prefixes in storage."""
    prefixes: list[str] = []
    try:
        user_prefixes = [
            k for k in storage.list_prefix("users/") if not k.endswith(".json")
        ]
    except Exception:
        return prefixes

    for user_prefix in user_prefixes:
        projects_prefix = user_prefix.rstrip("/") + "/projects/"
        try:
            for k in storage.list_prefix(projects_prefix):
                if not k.endswith(".json"):
                    prefixes.append(k.rstrip("/"))
        except Exception:
            continue
    return prefixes


def patch_model_file(storage, key: str, apply: bool) -> tuple[str | None, str | None]:
    """Returns (mpn, subtype) when patched; (None, None) otherwise."""
    try:
        data = storage.read_json(key)
    except Exception as exc:
        print(f"  ERROR reading {key}: {exc}")
        return None, None

    specs = data.get("specs")
    if not isinstance(specs, dict):
        return None, None

    target = SPECS_TYPE_TO_SUBTYPE.get(specs.get("specs_type"))
    if not target:
        return None, None
    if specs.get("component_subtype"):
        return None, None

    specs["component_subtype"] = target
    mpn = data.get("mpn") or key.rsplit("/", 1)[-1].removesuffix(".json")

    if apply:
        try:
            storage.write_json(key, data)
            print(f"  SET  {key}  → {target}")
        except Exception as exc:
            print(f"  ERROR writing {key}: {exc}")
            return None, None
    else:
        print(f"  WOULD SET  {key}  → {target}")
    return mpn, target


def patch_bom_summary(storage, bom_key: str, mpn_to_subtype: dict[str, str], apply: bool) -> int:
    """Patch row categories in-place. Returns number of rows updated."""
    try:
        data = storage.read_json(bom_key)
    except Exception:
        return 0
    rows = data if isinstance(data, list) else (data.get("rows") if isinstance(data, dict) else None)
    if not isinstance(rows, list):
        return 0

    updated = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("category"):
            continue
        mpn = row.get("mpn")
        if not mpn or mpn not in mpn_to_subtype:
            continue
        row["category"] = mpn_to_subtype[mpn]
        updated += 1

    if updated == 0:
        return 0

    if apply:
        try:
            storage.write_json(bom_key, data)
            print(f"  BOM  {bom_key}  → patched {updated} row(s)")
        except Exception as exc:
            print(f"  ERROR writing {bom_key}: {exc}")
            return 0
    else:
        print(f"  BOM WOULD PATCH  {bom_key}  → {updated} row(s)")
    return updated


def main():
    parser = argparse.ArgumentParser(
        description="Backfill component_subtype on typed passive specs + BOM summaries"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write changes (default is dry run)",
    )
    args = parser.parse_args()

    storage = get_storage()
    print(f"Storage backend: {type(storage).__name__}\n")

    # Pass 1: library/passives/ — patch each file standalone (no BOM owner).
    print("== Pass 1a: library/passives/ ==")
    lib_keys = [
        k for k in storage.list_recursive("library/passives/") if k.endswith(".json")
    ]
    print(f"Found {len(lib_keys)} library passive file(s)")
    lib_updated = 0
    for key in lib_keys:
        mpn, _ = patch_model_file(storage, key, args.apply)
        if mpn:
            lib_updated += 1

    # Pass 2: per-project models/ + bom_summary.json
    print("\n== Pass 2: per-project models/ + bom_summary.json ==")
    project_prefixes = list_project_prefixes(storage)
    print(f"Found {len(project_prefixes)} project(s)\n")

    models_updated = 0
    boms_patched = 0
    rows_patched = 0

    for prefix in project_prefixes:
        models_prefix = f"{prefix}/models/"
        bom_key = f"{prefix}/bom_summary.json"

        mpn_to_subtype: dict[str, str] = {}
        for key in storage.list_recursive(models_prefix):
            if not key.endswith(".json"):
                continue
            mpn, subtype = patch_model_file(storage, key, args.apply)
            if mpn:
                mpn_to_subtype[mpn] = subtype
                models_updated += 1

        if mpn_to_subtype and storage.exists(bom_key):
            n = patch_bom_summary(storage, bom_key, mpn_to_subtype, args.apply)
            if n:
                boms_patched += 1
                rows_patched += n

    print()
    verb = "updated" if args.apply else "would be updated"
    print(
        f"Done: {lib_updated} library model(s) {verb}; "
        f"{models_updated} per-project model(s) {verb}; "
        f"{boms_patched} bom_summary file(s) {verb} ({rows_patched} row(s))"
    )
    if not args.apply and (lib_updated or models_updated or rows_patched):
        print("Run with --apply to execute")


if __name__ == "__main__":
    main()
