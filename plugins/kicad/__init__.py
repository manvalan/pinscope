"""KiCad plugin package. Registers the action when loaded inside pcbnew."""

from __future__ import annotations

try:
    from . import pinscope_plugin  # noqa: F401
except ImportError:
    pass
