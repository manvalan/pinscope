"""Pinscope app version, sourced from frontend/content/changelog.md.

The changelog is the single source of truth for the user-facing version.
The Dockerfile copies it into the image at /app/changelog.md; locally we
fall back to the in-repo path.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


def _candidate_paths() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        Path("/app/changelog.md"),
        here.parent.parent / "frontend" / "content" / "changelog.md",
    ]


_VERSION_RE = re.compile(r"^##\s+(\d+\.\d+\.\d+)\b", re.MULTILINE)


@lru_cache(maxsize=1)
def get_pinscope_version() -> str:
    for path in _candidate_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            continue
        m = _VERSION_RE.search(text)
        if m:
            return m.group(1)
    return "unknown"


PINSCOPE_VERSION = get_pinscope_version()
