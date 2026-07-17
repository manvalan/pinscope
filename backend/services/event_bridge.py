"""Cross-process event bridge for pipeline progress.

Today the FastAPI API process and the pipeline worker (Cloud Run Job
execution, or a local subprocess in dev) live in different processes, so
the in-memory ``EventBroker`` in ``services.pipeline`` can't span them.

The bridge:

  * Worker writes one object per event to
    ``users/{user_id}/projects/{project_id}/events/{seq:010d}.json``.
    The object holds ``{seq, ts, event, data}``. The worker is the only
    writer for a given run, so its local monotonic ``seq`` counter
    needs no coordination.

  * API SSE handler tails the same prefix via ``StorageBackend.list_prefix_after``,
    yielding events in order until a terminal one arrives or the caller
    cancels.

This avoids appending to a single JSONL on GCS (no append API; full
rewrite or compose-per-event has worse semantics) and naturally survives
SSE reconnects (consumer just resumes from its last seen ``seq``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

from backend.services.projects import project_prefix
from backend.services.storage import StorageBackend

logger = logging.getLogger(__name__)


# Filename pattern: 10-digit zero-padded seq + .json. Lexicographic order
# matches numeric order so list_prefix_after pages cleanly.
_SEQ_WIDTH = 10
_FILENAME_FMT = f"{{seq:0{_SEQ_WIDTH}d}}.json"

# Terminal event names — the SSE loop stops on these.
TERMINAL_EVENTS = frozenset({
    "pipeline_complete",
    "pipeline_error",
    "pipeline_cancelled",
    "pipeline_paused",
})


def _events_prefix(user_id: str, project_id: str) -> str:
    return f"{project_prefix(user_id, project_id)}/events/"


def _seq_from_key(key: str) -> int | None:
    """Extract the integer seq from an event key; ``None`` on parse failure."""
    name = key.rsplit("/", 1)[-1]
    if not name.endswith(".json"):
        return None
    stem = name[:-5]
    try:
        return int(stem)
    except ValueError:
        return None


class GCSEventBroker:
    """Drop-in for the in-memory ``EventBroker`` that persists to storage.

    Same interface (``publish``, ``subscribe``, ``unsubscribe``,
    ``clear_history``) so the worker can swap it in for the module-level
    ``broker`` singleton without touching call sites. Subscription is a
    no-op — the API consumes events via :func:`tail_events` instead.
    """

    def __init__(self, storage: StorageBackend, user_id: str) -> None:
        self.storage = storage
        self.user_id = user_id
        # Per-project local counter. Workers handle one project per
        # execution, but the dict shape keeps parity with ``EventBroker``.
        self._seq: dict[str, int] = {}

    def subscribe(self, project_id: str) -> asyncio.Queue:
        # Workers never subscribe — only the API tails the GCS event log.
        # Returning an unfed queue is acceptable but raising is more
        # honest about the contract.
        raise NotImplementedError(
            "GCSEventBroker is publish-only; subscribers should call "
            "event_bridge.tail_events(...) instead."
        )

    def unsubscribe(self, project_id: str, q: asyncio.Queue) -> None:
        # No-op for symmetry with the in-memory broker.
        return

    def clear_history(self, project_id: str) -> None:
        """Wipe all prior event objects for this project.

        Called at the start of a fresh run so resumed/restarted runs
        don't intermix with stale events from earlier attempts.
        """
        prefix = _events_prefix(self.user_id, project_id)
        try:
            self.storage.delete_prefix(prefix)
        except Exception:
            logger.exception("failed to clear event history at %s", prefix)
        self._seq[project_id] = 0

    def publish(self, project_id: str, event: str, data: dict) -> None:
        seq = self._seq.get(project_id, 0)
        self._seq[project_id] = seq + 1
        key = _events_prefix(self.user_id, project_id) + _FILENAME_FMT.format(seq=seq)
        msg = {
            "seq": seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "data": data,
        }
        try:
            self.storage.write_json(key, msg)
        except Exception:
            # An event-write failure should never crash the pipeline.
            logger.exception("failed to write event %s to %s", event, key)


async def tail_events(
    storage: StorageBackend,
    user_id: str,
    project_id: str,
    *,
    poll_interval: float = 0.5,
    heartbeat_interval: float = 15.0,
) -> AsyncIterator[dict]:
    """Yield events from the GCS-backed event log in order.

    Stops yielding after a terminal event (``pipeline_complete``,
    ``pipeline_error``, ``pipeline_cancelled``). Emits a
    ``{"event": "heartbeat", "data": {}}`` synthetic event roughly every
    ``heartbeat_interval`` seconds when no real events arrive, matching
    the behaviour of the in-memory broker's SSE loop.

    The caller is expected to handle disconnects/cancellations and
    secondary terminal-detection (``meta.status``, Cloud Run execution
    state) on top of this iterator.
    """
    prefix = _events_prefix(user_id, project_id)
    last_seen_key: str | None = None
    last_emit_ts = 0.0

    while True:
        try:
            keys = storage.list_prefix_after(prefix, after_key=last_seen_key)
        except Exception:
            logger.exception("event tail: list_prefix_after failed for %s", prefix)
            keys = []

        emitted_any = False
        for key in keys:
            try:
                msg = storage.read_json(key)
            except Exception:
                logger.exception("event tail: read_json failed for %s", key)
                continue
            yield msg
            emitted_any = True
            last_seen_key = key
            last_emit_ts = asyncio.get_event_loop().time()
            if msg.get("event") in TERMINAL_EVENTS:
                return

        now = asyncio.get_event_loop().time()
        if not emitted_any and now - last_emit_ts >= heartbeat_interval:
            yield {"event": "heartbeat", "data": {}}
            last_emit_ts = now

        await asyncio.sleep(poll_interval)
