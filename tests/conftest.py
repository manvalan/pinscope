"""Shared pytest fixtures — temp-dir storage backend + clean import path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the repo root is on sys.path so `import backend.*` works whether
# pytest is invoked from the repo root or a subdirectory.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _disable_llm_post_passes(monkeypatch):
    """Keep the LLM normalize + cross-IC dedup passes off by default in tests.

    Both ``normalize_findings_async`` and ``dedupe_cross_ic_findings_async``
    import ``call_with_fallback`` into their own module namespace, so a test
    that does ``monkeypatch.setattr(validation, "call_with_fallback", fake)``
    does NOT intercept them — they would reach the real provider and make a
    live API call mid-test. The dedicated unit tests exercise these passes via
    their pure builder functions (``_build_normalized`` / ``_build_deduped``)
    directly, so nothing needs them enabled through ``validate_design_async``.
    A test that genuinely wants them on can re-enable with its own
    ``monkeypatch.setattr`` (which runs after this autouse fixture)."""
    from backend.config import settings

    monkeypatch.setattr(settings, "normalize_findings_enabled", False, raising=False)
    monkeypatch.setattr(settings, "cross_ic_dedup_enabled", False, raising=False)


@pytest.fixture
def storage(tmp_path):
    """Return a LocalStorageBackend rooted at a fresh temp directory."""
    from backend.services.storage import LocalStorageBackend

    return LocalStorageBackend(tmp_path)
