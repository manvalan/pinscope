"""Shared utility functions for the pinscopex core library."""

from __future__ import annotations

import re


def safe_mpn(mpn: str) -> str:
    """Sanitize an MPN string for use in filenames and storage keys."""
    return mpn.replace("/", "_").replace(":", "_")


def natural_sort_key(s: str) -> tuple:
    """Sort key for natural ordering: R1, R2, R10 (not R1, R10, R2)."""
    parts: list[int | str] = []
    for chunk in re.split(r"(\d+)", s):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            parts.append(chunk.lower())
    return tuple(parts)
