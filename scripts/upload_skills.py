#!/usr/bin/env python3
"""Upload or update extraction skills on the Claude Console platform.

Usage:
    python3 scripts/upload_skills.py              # Create new skills
    python3 scripts/upload_skills.py --update      # Create new versions of existing skills
    python3 scripts/upload_skills.py --list         # List current skills

Requires ANTHROPIC_API_KEY environment variable.
Reads/writes skill IDs to backend/skills_manifest.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load API key from backend .env if not already in environment
_env_file = PROJECT_ROOT / "backend" / ".env"
if _env_file.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY=") and not line.startswith("#"):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip("'\"")
            break
SKILLS_DIR = PROJECT_ROOT / "skills"
MANIFEST_PATH = PROJECT_ROOT / "backend" / "skills_manifest.json"

SKILLS = [
    {
        "directory": "extract-pintable",
        "display_title": "Extract Pin Table",
    },
    {
        "directory": "extract-pattern",
        "display_title": "Extract Passive Pattern",
    },
    {
        "directory": "extract-specs",
        "display_title": "Extract Component Specs",
    },
]


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nManifest written to {MANIFEST_PATH}")


def skill_file_tuples(directory: str) -> list[tuple[str, bytes]]:
    """Return (directory/filename, content) tuples for all files in a skill directory.

    The API requires files to be in a top-level directory with SKILL.md at its root.
    """
    skill_dir = SKILLS_DIR / directory
    files = []
    for path in sorted(skill_dir.iterdir()):
        if path.is_file():
            files.append((f"{directory}/{path.name}", path.read_bytes()))
    return files


def _bump_minor_version(v: str) -> str:
    """Bump the minor segment of a semver string, reset patch to 0."""
    major, minor, patch = v.split(".")
    return f"{major}.{int(minor) + 1}.0"


def create_skills(client: anthropic.Anthropic) -> None:
    """Create new skills on the platform."""
    manifest = load_manifest()

    # Initialize default_model_version if absent
    if "default_model_version" not in manifest:
        manifest["default_model_version"] = "1.0.0"
        print(f"  Initialized default_model_version: 1.0.0")

    for skill in SKILLS:
        name = skill["directory"]
        if name in manifest:
            print(f"  {name}: already exists (skill_id={manifest[name]['skill_id']}), skipping. Use --update to create a new version.")
            continue

        print(f"  Creating {name}...")
        files = skill_file_tuples(name)
        result = client.beta.skills.create(
            display_title=skill["display_title"],
            files=files,
        )
        manifest[name] = {
            "skill_id": result.id,
            "latest_version": result.latest_version,
            "display_title": skill["display_title"],
        }
        print(f"    skill_id: {result.id}")
        print(f"    version:  {result.latest_version}")

    save_manifest(manifest)


def update_skills(client: anthropic.Anthropic) -> None:
    """Create new versions for existing skills."""
    manifest = load_manifest()

    for skill in SKILLS:
        name = skill["directory"]
        if name not in manifest:
            print(f"  {name}: not yet created, run without --update first.")
            continue

        skill_id = manifest[name]["skill_id"]
        print(f"  Updating {name} (skill_id={skill_id})...")
        files = skill_file_tuples(name)
        result = client.beta.skills.versions.create(
            skill_id=skill_id,
            files=files,
        )
        manifest[name]["latest_version"] = result.version
        print(f"    new version: {result.version}")

    # Bump default_model_version minor (new skill → new extraction schema)
    old_v = manifest.get("default_model_version", "1.0.0")
    new_v = _bump_minor_version(old_v)
    manifest["default_model_version"] = new_v
    print(f"\n  default_model_version bumped: {old_v} → {new_v}")

    save_manifest(manifest)


def list_skills(client: anthropic.Anthropic) -> None:
    """List skills on the platform."""
    manifest = load_manifest()
    if not manifest:
        print("  No skills in manifest. Run without flags to create them.")
        return

    for name, info in manifest.items():
        skill_id = info["skill_id"]
        print(f"\n  {name}:")
        print(f"    skill_id: {skill_id}")
        try:
            skill = client.beta.skills.retrieve(skill_id)
            print(f"    display_title: {skill.display_title}")
            print(f"    latest_version: {skill.latest_version}")
            versions = client.beta.skills.versions.list(skill_id=skill_id)
            print(f"    versions: {[v.version for v in versions.data]}")
        except Exception as e:
            print(f"    error: {e}")

    print(f"\n  Manifest: {MANIFEST_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload extraction skills to Claude Console")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--update", action="store_true", help="Create new versions of existing skills")
    group.add_argument("--list", action="store_true", help="List current skills and versions")
    args = parser.parse_args()

    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY env var

    if args.list:
        print("Listing skills...")
        list_skills(client)
    elif args.update:
        print("Updating skills (new versions)...")
        update_skills(client)
    else:
        print("Creating skills...")
        create_skills(client)


if __name__ == "__main__":
    main()
