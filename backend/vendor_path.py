"""Put vendored ImpedenceFinder on sys.path (closed-form package only)."""

from __future__ import annotations

import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parents[1] / "vendor"


def ensure_impedancefinder() -> None:
    root = str(VENDOR_DIR)
    if root not in sys.path:
        sys.path.insert(0, root)
