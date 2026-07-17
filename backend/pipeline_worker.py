"""Pipeline worker entrypoint — runs as a Cloud Run Job execution.

Invoked by Cloud Run Jobs (prod) or as a child subprocess (local dev).
Reads execution parameters from environment variables, swaps the
in-memory event broker for the GCS-backed one, and dispatches to either
``run_pipeline`` (full run) or ``run_regen_pipeline`` (admin regen).

Required env vars:
  PROJECT_ID    — project to run
  USER_ID       — owner user id (Clerk sub or "local")

Optional env vars:
  RESUME           "1"/"0"  — resume a paused run from its checkpoint
  FREE             "1"/"0"  — admin-initiated free run (no charge)
  MODE             "run" (default) | "regen"
  REGEN_STAGES     comma-separated, e.g. "derating" (regen mode only)
  EXECUTION_NAME   Cloud Run execution resource name (purely for log
                   correlation — the API already wrote it onto
                   ``ProjectMeta.execution_name`` at enqueue time)

This module **must not** import :mod:`backend.main` — the FastAPI
lifespan would attempt to wire up shutdown handlers we don't want here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from backend.config import settings
from backend.services import event_bridge as event_bridge
from backend.services import pipeline as pipeline_svc
from backend.services.storage import LocalStorageBackend, StorageBackend


def _build_storage() -> StorageBackend:
    if settings.use_gcs:
        from backend.services.storage_gcs import GCSStorageBackend

        return GCSStorageBackend(settings.gcs_bucket)
    return LocalStorageBackend(settings.data_dir)


def _required_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"missing required env var: {name}")
    return val


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


async def _run() -> None:
    project_id = _required_env("PROJECT_ID")
    user_id = _required_env("USER_ID")
    resume = _bool_env("RESUME")
    free = _bool_env("FREE")
    mode = os.environ.get("MODE", "run").strip().lower() or "run"
    execution_name = os.environ.get("EXECUTION_NAME", "").strip()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [worker %(name)s] %(message)s",
    )
    log = logging.getLogger("backend.pipeline_worker")
    log.info(
        "starting worker mode=%s project=%s user=%s resume=%s free=%s execution=%s",
        mode, project_id, user_id, resume, free, execution_name or "(none)",
    )

    storage = _build_storage()

    # Swap in the GCS-backed broker so events written from this process
    # are visible to any API instance tailing the event log.
    pipeline_svc.set_broker(event_bridge.GCSEventBroker(storage, user_id))

    # Fresh runs wipe the prior event log so the SSE consumer doesn't
    # mix old events into the new run. Resume keeps the prior log so
    # users see the full history.
    if not resume:
        pipeline_svc.broker.clear_history(project_id)

    if mode == "run":
        await pipeline_svc.run_pipeline(
            storage, user_id, project_id, resume=resume, free=free,
        )
    elif mode == "regen":
        stages_raw = os.environ.get("REGEN_STAGES", "").strip()
        stages = [s for s in (s.strip() for s in stages_raw.split(",")) if s]
        if not stages:
            raise SystemExit("REGEN_STAGES must list at least one stage in regen mode")
        await pipeline_svc.run_regen_pipeline(
            storage, user_id, project_id, stages,
        )
    else:
        raise SystemExit(f"unknown MODE={mode!r}; expected 'run' or 'regen'")


def main() -> None:
    try:
        asyncio.run(_run())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        # Local dev convenience — the run_pipeline cancel handler will
        # have already transitioned the project on SIGTERM.
        sys.exit(130)
    except BaseException as exc:  # pragma: no cover — last-mile safety
        # The pipeline's own ``except Exception`` already logs and writes
        # ``status=error`` for the project. This catch only exists so a
        # truly unhandled BaseException (e.g. SystemExit during boot
        # before run_pipeline starts) still surfaces as a non-zero exit
        # code, which Cloud Run records as "Failed" on the execution.
        logging.exception("worker crashed before run_pipeline cleanup: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
