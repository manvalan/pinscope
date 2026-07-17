"""Pipeline orchestrator — runs all stages and emits SSE events.

Stages:
  1. Parse BOM → classify IC, discrete/simple, and passive MPNs
  2. IC Pintable Extraction (per MPN) — pin names for graph enrichment
  2.5. Simple Component Specs Extraction (per MPN with datasheet)
  3. Passive Extraction: pattern-based (per MPN group), then specs fallback (per MPN)
  4. Build Design Graph
  5. Direct Datasheet Review — per-IC review with PDF + circuit neighborhood

This module no longer spawns the pipeline as a FastAPI ``BackgroundTask``.
The API enqueues a Cloud Run Job execution (or, in dev, a subprocess) via
:mod:`backend.services.job_runner`; the actual ``run_pipeline`` and
``run_regen_pipeline`` coroutines are imported and invoked by
:mod:`backend.pipeline_worker`. Progress events flow through the
module-level ``broker`` — in the API it stays an in-memory
:class:`EventBroker` (used only by tests / local importers); in the
worker it is replaced with a :class:`GCSEventBroker` via :func:`set_broker`
before any pipeline code runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable


from backend.pinscopex.models import ComponentType
from backend.pinscopex.utils import natural_sort_key, safe_mpn
from backend.pinscopex.bom_summary import build_bom_summary
from backend.pinscopex.derating import build_derating_table
from backend.pinscopex.validate import _load_datasheets
from backend.pinscopex.graph import build_graph
from backend.pinscopex.parsers import parse_bom, parse_netlist_any
from backend.pinscopex.resolve_passives import SkippedItem, load_patterns, resolve_mpn
from backend.pinscopex.taxonomy import SIMPLE_TYPES, type_for_ref

from backend.config import settings
from backend.services import admin_settings as settings_svc
from backend.services.billing_hook import InsufficientCredits, get_billing
from backend.services.datasheet_store import compute_md5_from_path, store_datasheet
from backend.services import extraction, projects as proj_svc
from backend.services.api_logs import ApiLogger, total_cost
from backend.services.cost_estimator import estimate_stage_cost_usd
from backend.services.storage import StorageBackend
from backend.services.validation import validate_design_async

logger = logging.getLogger(__name__)


_GIT_COMMIT: str | None = None


def _ic_descriptions(extracted_dir: Path) -> dict[str, str]:
    """Read ``package_info.description`` from extracted IC constraints, keyed
    by MPN. Used to populate the BOM Specs column for ICs with a one-line
    "what this chip does" summary. Best-effort — missing or unreadable files
    are skipped silently."""
    out: dict[str, str] = {}
    try:
        for mpn, c in _load_datasheets(extracted_dir).items():
            desc = c.package_info.description if c.package_info else None
            if desc:
                out[mpn] = desc
    except Exception:
        logger.exception("ic_descriptions: load failed for %s", extracted_dir)
    return out


def _git_commit() -> str:
    """Short git SHA of the running code, resolved once and cached.
    Stamped into per-IC review traces. Never raises."""
    global _GIT_COMMIT
    if _GIT_COMMIT is None:
        try:
            import subprocess

            _GIT_COMMIT = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=Path(__file__).resolve().parent,
            ).stdout.strip() or "unknown"
        except Exception:
            logger.exception("could not resolve git commit for review traces")
            _GIT_COMMIT = "unknown"
    return _GIT_COMMIT


# ---------------------------------------------------------------------------
# SSE Event Broker
# ---------------------------------------------------------------------------

class EventBroker:
    """In-memory pub/sub for SSE events, keyed by project_id.

    Buffers all events per project so late subscribers (e.g. after a page
    navigation) receive the full history before seeing live events.
    """

    def __init__(self):
        self._queues: dict[str, list[asyncio.Queue]] = {}
        self._history: dict[str, list[dict]] = {}

    def subscribe(self, project_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        # Replay buffered events so the subscriber catches up
        for msg in self._history.get(project_id, []):
            q.put_nowait(msg)
        self._queues.setdefault(project_id, []).append(q)
        return q

    def unsubscribe(self, project_id: str, q: asyncio.Queue) -> None:
        qs = self._queues.get(project_id, [])
        if q in qs:
            qs.remove(q)
        if not qs:
            self._queues.pop(project_id, None)

    def clear_history(self, project_id: str) -> None:
        self._history.pop(project_id, None)

    def publish(self, project_id: str, event: str, data: dict) -> None:
        msg = {"event": event, "data": data}
        self._history.setdefault(project_id, []).append(msg)
        for q in self._queues.get(project_id, []):
            q.put_nowait(msg)


broker: EventBroker = EventBroker()


def set_broker(b: EventBroker) -> None:
    """Replace the module-level broker.

    Called by :mod:`backend.pipeline_worker` at startup to swap in the
    GCS-backed broker so events written from the worker are visible to
    the API's SSE handler. Must be called *before* :func:`run_pipeline`
    or :func:`run_regen_pipeline`.
    """
    global broker
    broker = b


# Per-process cancel-flag cache: re-reading the project meta from GCS on
# every Claude API call would dominate latency. The worker's cancel gate
# (inside ``_charge_for_logs``) refreshes at most every
# ``_CANCEL_POLL_INTERVAL_S`` seconds.
_CANCEL_POLL_INTERVAL_S = 3.0


class CancelRequested(Exception):
    """Raised by the cancel gate when ``meta.cancel_requested == True``.

    Bubbles up through the stage loop; the top-level run handler catches
    it, emits ``pipeline_cancelled``, transitions the project status to
    ``cancelled``, and exits.
    """


def _cancel_gate_check(ctx: PipelineContext) -> None:
    """Check the cancel flag on disk; raise if set.

    Caches the last poll time on the context so we don't hammer GCS.
    Uses ``time.monotonic`` rather than the asyncio event loop's clock
    so callers can invoke this from sync test code without first
    spinning up an event loop.
    """
    import time as _time

    last = getattr(ctx, "_last_cancel_poll", 0.0)
    now = _time.monotonic()
    if now - last < _CANCEL_POLL_INTERVAL_S:
        return
    ctx._last_cancel_poll = now  # type: ignore[attr-defined]
    try:
        meta = proj_svc.get_project(ctx.storage, ctx.user_id, ctx.project_id)
    except Exception:
        # Storage hiccups must not abort the pipeline.
        return
    if meta is not None and meta.cancel_requested:
        raise CancelRequested(f"cancel requested for {ctx.project_id}")


# ---------------------------------------------------------------------------
# Pipeline Workspace
# ---------------------------------------------------------------------------

class PipelineWorkspace:
    """Downloads project files from storage to a temp dir for pipeline execution.

    The pinscopex core library operates on local paths. This context manager
    downloads inputs at enter, provides local paths, and uploads results at exit.
    """

    def __init__(
        self,
        storage: StorageBackend,
        user_id: str,
        project_id: str,
    ) -> None:
        self.storage = storage
        self.user_id = user_id
        self.project_id = project_id
        self.prefix = proj_svc.project_prefix(user_id, project_id)
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self.local_dir: Path = Path()

    async def __aenter__(self) -> PipelineWorkspace:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.local_dir = Path(self._tmpdir.name)

        # Create subdirectories
        (self.local_dir / "uploads" / "datasheets").mkdir(parents=True)
        (self.local_dir / "extracted").mkdir(parents=True)
        (self.local_dir / "patterns").mkdir(parents=True)
        (self.local_dir / "models").mkdir(parents=True)
        (self.local_dir / "taxonomy").mkdir(parents=True)

        # Download project files from storage
        all_keys = self.storage.list_recursive(self.prefix)
        for key in all_keys:
            # key is like users/{uid}/projects/{pid}/uploads/bom.csv
            # We want the relative part after the project prefix
            rel = key[len(self.prefix) + 1:]  # strip prefix + trailing /
            local_path = self.local_dir / rel
            self.storage.download_to_local(key, local_path)

        # Download taxonomy files from storage
        taxonomy_keys = self.storage.list_prefix("taxonomy/")
        for key in taxonomy_keys:
            if key.endswith(".json"):
                filename = key.rsplit("/", 1)[-1]
                self.storage.download_to_local(key, self.local_dir / "taxonomy" / filename)

        # Seed from repo taxonomy if storage had no taxonomy files yet
        local_tax = self.local_dir / "taxonomy"
        if not any(local_tax.glob("*.json")):
            repo_tax = settings.taxonomy_dir
            if repo_tax.is_dir():
                for f in repo_tax.glob("*.json"):
                    shutil.copy2(f, local_tax / f.name)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            # Upload outputs back to storage
            self._upload_dir("extracted")
            self._upload_dir("patterns")
            self._upload_dir("models")
            self._upload_file("design_graph.json")
            self._upload_file("bom_summary.json")
            self._upload_file("derating.json")
            self._upload_file("report.json")
            self._upload_file("api_logs.jsonl")

            # Merge taxonomy: read current from storage, add any new entries
            # from this run, write back.  This avoids clobbering subtypes
            # that a concurrent pipeline added while we were running.
            tax_dir = self.local_dir / "taxonomy"
            if tax_dir.is_dir():
                for f in tax_dir.iterdir():
                    if f.is_file() and f.suffix == ".json":
                        local_data = json.loads(f.read_text())
                        local_subtypes = local_data.get("subtypes", {})
                        storage_key = f"taxonomy/{f.name}"

                        if self.storage.exists(storage_key):
                            current = self.storage.read_json(storage_key)
                            merged = current.get("subtypes", {})
                            for key, entry in local_subtypes.items():
                                if key not in merged:
                                    merged[key] = entry
                                else:
                                    # Backfill fields the local run generated
                                    # (e.g. specs_schema) that the
                                    # storage copy is missing.
                                    for field, value in entry.items():
                                        if field not in merged[key]:
                                            merged[key][field] = value
                            current["subtypes"] = merged
                            self.storage.write_json(storage_key, current)
                        else:
                            self.storage.write_json(storage_key, local_data)

        if self._tmpdir:
            self._tmpdir.cleanup()

    def _upload_dir(self, subdir: str) -> None:
        """Upload all files in a subdirectory back to storage."""
        local = self.local_dir / subdir
        if not local.is_dir():
            return
        for f in local.rglob("*"):
            if f.is_file():
                rel = f.relative_to(self.local_dir)
                key = f"{self.prefix}/{rel}"
                self.storage.upload_from_local(f, key)

    def _upload_file(self, name: str) -> None:
        """Upload a single file back to storage if it exists."""
        local = self.local_dir / name
        if local.is_file():
            self.storage.upload_from_local(local, f"{self.prefix}/{name}")

    def local_path(self, rel: str) -> Path:
        """Get a local path within the workspace."""
        return self.local_dir / rel

    def netlist_local_path(self) -> Path:
        """Local path of whichever netlist file was synced (``.asc`` or ``.edn``).

        Pipeline workspace mirrors the entire project prefix, so whichever
        format the user uploaded lands locally with its original extension.
        Falls back to ``uploads/netlist.asc`` if neither exists — downstream
        code will raise a clearer error when it tries to read the missing
        file than a ``None`` return would.
        """
        for ext in ("asc", "edn"):
            p = self.local_dir / "uploads" / f"netlist.{ext}"
            if p.exists():
                return p
        return self.local_dir / "uploads" / "netlist.asc"

    @property
    def taxonomy_dir(self) -> Path:
        return self.local_dir / "taxonomy"


# ---------------------------------------------------------------------------
# Pipeline context — shared state threaded through all stage functions
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    """All shared state for a single pipeline run.

    Infrastructure fields are set up once in ``run_pipeline`` before the stage
    loop starts.  Stage-output fields are written by each stage and read by
    later ones.  To reorder stages, change ``PIPELINE_STAGES`` below.
    """

    # Infrastructure (set up once before the stage loop)
    storage: StorageBackend
    user_id: str
    project_id: str
    ws: PipelineWorkspace
    api_logger: ApiLogger
    meta: Any  # ProjectMeta
    min_ver: str  # minimum extraction model version for cache freshness

    # Accumulated across all stages
    skipped: list[SkippedItem] = field(default_factory=list)

    # Stage outputs — each stage writes here; later stages read
    ic_mpns: dict[str, list[str]] = field(default_factory=dict)
    passive_mpns: dict[str, list[str]] = field(default_factory=dict)
    # Captured BOM Value per passive MPN. Used as a last-resort fallback when
    # the MPN column actually contains a value token (e.g. "10uF") — we resolve
    # the primary numeric value from here without saving to the shared library.
    passive_values: dict[str, str] = field(default_factory=dict)
    simple_mpns: dict[str, list[str]] = field(default_factory=dict)
    simple_mpn_types: dict[str, str] = field(default_factory=dict)
    # Cached purple-parts payload (description, category, subcategory, manufacturer,
    # package, ...) keyed by *resolved* MPN. Populated by _resolve_lcsc_codes during
    # BOM parse; consumed by passive extraction as a first-pass auto-resolve source
    # before falling through to DigiKey.
    lcsc_data: dict[str, dict] = field(default_factory=dict)
    ref_col: str = "Reference"
    mpn_col: str = "Manufacturer Part Number"
    patterns: list = field(default_factory=list)  # loaded + mutated by passive_extraction
    graph: Any | None = None   # DesignGraph
    report: Any | None = None  # ValidationReport

    # Credit-gate state
    paused: bool = False
    pause_stage: str | None = None
    pause_unit_id: str | None = None
    pause_last_completed: str | None = None
    credits_spent: float = 0.0
    completed_review_refs: set[str] = field(default_factory=set)
    # Every IC ref the validation stage plans to review (has datasheet PDF).
    # Populated at validation stage start so a pause checkpoint can expose
    # what's left. Empty for runs that pause before validation.
    all_review_refs: list[str] = field(default_factory=list)

    # Admin-initiated free run: skip credit gate and cost accrual. Every
    # API call is still made (and the USD cost is still recorded in logs),
    # but nothing is charged to the user's balance.
    free: bool = False


# ---------------------------------------------------------------------------
# Credit gate — checked before each expensive sub-unit
# ---------------------------------------------------------------------------


def _check_credit_gate(
    ctx: PipelineContext, stage: str, unit_id: str, estimated_cost_usd: float,
) -> bool:
    """Return True if the run can spend ``estimated_cost_usd`` on this unit.

    On insufficient balance, sets ``ctx.paused`` and records where we stopped.
    The caller should break out of its loop when this returns False.
    """
    if ctx.paused:
        return False
    # Admin-initiated free runs never hit the balance gate.
    if ctx.free:
        return True
    billing = get_billing()
    required_credits = billing.credits_for_api_cost(estimated_cost_usd)
    if required_credits <= 0:
        return True  # Cached / free work — no balance check needed
    balance = billing.get_balance(ctx.storage, ctx.user_id)
    if balance < required_credits:
        ctx.paused = True
        ctx.pause_stage = stage
        ctx.pause_unit_id = unit_id
        return False
    return True


def _charge_for_logs(ctx: PipelineContext, before_count: int) -> None:
    """Charge the user for all API log entries added since ``before_count``.

    Reads the logger's entry list directly — each entry already has
    ``cost_usd`` and ``credits_charged`` populated by ``ApiLogger.log``.

    If the user has auto top-up enabled and the charge dropped their
    balance below the threshold, fires an off-session top-up attempt.

    Also acts as the worker's cancel gate: after every Claude API call
    we re-read the project meta and bail with :class:`CancelRequested`
    when the user has requested cancellation. Polling is throttled in
    :func:`_cancel_gate_check`, so this is cheap.
    """
    # Cancel-gate check first — if the user pressed Cancel, don't spend
    # any more on this run. Cheap: throttled to one GCS read per few
    # seconds. May raise; the top-level run handler catches and cleans up.
    _cancel_gate_check(ctx)

    new_entries = ctx.api_logger.entries[before_count:]
    total_credits = sum(float(e.get("credits_charged") or 0) for e in new_entries)
    if total_credits <= 0:
        return
    # Work for this unit is already done — charge the full amount even if
    # it exceeds the current balance.  The credit gate in
    # ``_check_credit_gate`` prevents us from *starting* a new unit once
    # the balance is insufficient, so only the unit currently in flight
    # (e.g. an IC review) can push the ledger negative.
    amount = round(total_credits, 4)
    unit_id = new_entries[-1].get("identifier") if new_entries else None
    stage = new_entries[-1].get("stage") if new_entries else None
    billing = get_billing()
    try:
        billing.charge(
            ctx.storage, ctx.user_id, amount,
            reason="pipeline_charge",
            run_id=ctx.project_id,
            unit_id=f"{stage}:{unit_id}" if stage else None,
            allow_overdraft=True,
        )
        ctx.credits_spent += amount
        broker.publish(
            ctx.project_id, "credits_update",
            {
                "credits_spent": round(ctx.credits_spent, 4),
                "balance_after": round(billing.get_balance(ctx.storage, ctx.user_id), 4),
                "delta": round(amount, 4),
                "stage": stage,
                "unit_id": unit_id,
            },
        )
    except InsufficientCredits:
        # Shouldn't happen because we took min(amount, balance); log and move on.
        pass

    # Fire auto top-up if configured.  It runs as a background task so the
    # pipeline isn't blocked by Stripe round-trips.  On failure we publish
    # an SSE event so the progress page can show an in-app toast without
    # waiting on email delivery.
    try:
        async def _run_and_notify() -> None:
            failure = await billing.maybe_auto_topup(ctx.storage, ctx.user_id)
            if failure:
                broker.publish(ctx.project_id, "auto_topup_failed", failure)

        asyncio.create_task(_run_and_notify())
    except Exception:
        pass


def _charge_private_logger(ctx: PipelineContext, private: ApiLogger) -> None:
    """Merge a concurrent unit's private ``ApiLogger`` into the shared log and
    charge for exactly its entries.

    Concurrent stages (IC extraction, review) give each in-flight unit its own
    ``ApiLogger`` so that ``_charge_for_logs``' index slice can't mix one unit's
    API calls with another's. This runs synchronously — there is no ``await``
    between capturing ``before`` and the charge — so under asyncio it is atomic:
    no other coroutine can append to ``ctx.api_logger.entries`` in that window,
    and the slice is exactly this unit's entries.
    """
    before = len(ctx.api_logger.entries)
    ctx.api_logger.entries.extend(private.entries)
    _charge_for_logs(ctx, before)


async def _paused_stage_publish(ctx: PipelineContext, stage: str, reason: str) -> None:
    broker.publish(ctx.project_id, "step_update",
                   {"stage": stage, "status": "paused",
                    "detail": reason})


# ---------------------------------------------------------------------------
# Stage functions — one per UI step
# ---------------------------------------------------------------------------


async def _resolve_lcsc_codes(ctx: PipelineContext, bom: dict[str, dict]) -> None:
    """Convert LCSC part numbers in `bom` to MPNs via the purple-parts API.

    Mutates `bom` in place: any row whose `mpn` field is empty (but `lcsc`
    is set) or whose `mpn` itself looks like an LCSC code gets its `mpn`
    field populated from the lookup. Rows that don't resolve are left
    untouched — the existing DigiKey/Haiku paths still handle them.

    No-op when purple-parts is not configured (`settings.use_purple_parts`).
    """
    if not settings.use_purple_parts:
        return

    from backend.services.purple_parts import is_lcsc_code, lookup_lcsc_batch

    # Backstop only: cover the unambiguous case where the dedicated `LCSC`
    # column is populated and the MPN slot is empty. The primary path is
    # upload-time column-level resolution in routers/projects.py:upload_bom,
    # which rewrites the stored BOM before any pipeline run. Mixed BOMs are
    # explicitly out of scope — users must pick one representation per
    # column, so per-row MPN-shape detection at this point would be noise.
    todo: list[tuple[str, str]] = []
    for ref, info in bom.items():
        mpn = (info.get("mpn") or "").strip()
        lcsc = (info.get("lcsc") or "").strip()
        if not mpn and is_lcsc_code(lcsc):
            todo.append((ref, lcsc))

    if not todo:
        return

    unique_codes = sorted({code for _, code in todo})
    broker.publish(
        ctx.project_id, "step_update",
        {"stage": "bom_parse", "status": "running",
         "detail": f"Resolving {len(unique_codes)} LCSC code(s) via purple-parts"},
    )

    resolved = await lookup_lcsc_batch(unique_codes)

    hits = 0
    for ref, code in todo:
        part = resolved.get(code)
        if part and part.get("mpn"):
            mpn = part["mpn"]
            bom[ref]["mpn"] = mpn
            # Cache the rich payload keyed by the resolved MPN so downstream
            # passive extraction can skip DigiKey when LCSC already has the
            # description + category Haiku needs.
            ctx.lcsc_data.setdefault(mpn, part)
            hits += 1

    logger.info(
        "purple-parts: resolved %d/%d LCSC codes (covered %d BOM refs)",
        hits, len(unique_codes), len(todo),
    )


async def _stage_bom_parse(ctx: PipelineContext) -> None:
    """Stage 1 — Parse BOM and classify components by type."""
    broker.publish(ctx.project_id, "step_update",
                   {"stage": "bom_parse", "status": "running"})

    col_map = ctx.meta.bom_columns or {}
    ctx.ref_col = col_map.get("reference", "Reference")
    ctx.mpn_col = col_map.get("mpn", "Manufacturer Part Number")

    bom_path = ctx.ws.local_path("uploads/bom.csv")
    bom = parse_bom(str(bom_path), reference_col=ctx.ref_col, mpn_col=ctx.mpn_col)

    await _resolve_lcsc_codes(ctx, bom)

    for ref, info in sorted(bom.items()):
        mpn = info.get("mpn")
        if not mpn:
            continue
        typ = type_for_ref(ref)
        if typ == "ic":
            ctx.ic_mpns.setdefault(mpn, []).append(ref)
        elif typ == "passive":
            ctx.passive_mpns.setdefault(mpn, []).append(ref)
            val = (info.get("value") or "").strip()
            if val and not ctx.passive_values.get(mpn):
                ctx.passive_values[mpn] = val
        elif typ and typ in SIMPLE_TYPES:
            ctx.simple_mpns.setdefault(mpn, []).append(ref)
            ctx.simple_mpn_types[mpn] = typ

    proj_svc.update_project(
        ctx.storage, ctx.user_id, ctx.project_id,
        component_mpns={
            "ic": list(ctx.ic_mpns.keys()),
            "passive": list(ctx.passive_mpns.keys()),
            "simple": list(ctx.simple_mpns.keys()),
        },
    )

    # Quick netlist parse for net count (used in admin email). Auto-detect
    # PADS vs EDIF and honor any sub-design filter the user picked, so the
    # email reports the count for the slice the pipeline will actually review.
    netlist_path = ctx.ws.netlist_local_path()
    _, nets, _ = parse_netlist_any(
        str(netlist_path),
        known_refs=set(bom.keys()),
        include_subdesigns=(
            set(ctx.meta.netlist_subdesigns)
            if ctx.meta.netlist_subdesigns is not None
            else None
        ),
    )

    broker.publish(ctx.project_id, "step_update",
                   {"stage": "bom_parse", "status": "complete",
                    "detail": f"{len(bom)} refs, {len(ctx.ic_mpns)} ICs, "
                              f"{len(ctx.simple_mpns)} discrete/simple, {len(ctx.passive_mpns)} passives"})

    # Notify admin that a pipeline started (fire-and-forget)
    from backend.services.email import send_pipeline_started_email
    try:
        await send_pipeline_started_email(
            user_id=ctx.user_id,
            project_name=ctx.meta.name,
            project_id=ctx.project_id,
            num_components=len(bom),
            num_nets=len(nets),
            num_ics=len(ctx.ic_mpns),
            num_passives=len(ctx.passive_mpns),
            num_simple=len(ctx.simple_mpns),
        )
    except Exception:
        pass  # send_pipeline_started_email handles errors internally


async def _stage_ic_extraction(ctx: PipelineContext) -> None:
    """Stage 2 — Extract IC pin tables from datasheets."""
    extracted_dir = ctx.ws.local_path("extracted")

    # Pre-categorize: workspace cache, library cache, or needs extraction
    _ic_cache: dict[str, tuple] = {}
    _ic_new_count = 0
    for mpn in ctx.ic_mpns:
        safe = safe_mpn(mpn)
        json_path = extracted_dir / f"{safe}.json"
        if json_path.is_file():
            existing = json.loads(json_path.read_text())
            if existing.get("pintable"):
                ws_ver = existing.get("model_version", "0.0.0")
                if not settings_svc.version_is_stale(ws_ver, ctx.min_ver):
                    _ic_cache[mpn] = ("workspace",)
                    continue
        lib_key = proj_svc.library_has_extraction(ctx.storage, mpn, min_version=ctx.min_ver)
        if lib_key:
            _ic_cache[mpn] = ("library", lib_key)
            continue
        _ic_new_count += 1

    broker.publish(ctx.project_id, "step_update",
                   {"stage": "ic_extraction", "status": "running",
                    "total_new": _ic_new_count})

    # Phase 1 (sequential, read-only, no API cost): resolve cached MPNs and
    # locate datasheet PDFs. Cache-miss MPNs are collected for concurrent
    # extraction in Phase 2.
    pending: list[tuple[str, str, Path, Path]] = []  # (mpn, safe, json_path, pdf_path)
    for mpn, refs in ctx.ic_mpns.items():
        safe = safe_mpn(mpn)
        json_path = extracted_dir / f"{safe}.json"

        _cached = _ic_cache.get(mpn)
        if _cached:
            if _cached[0] == "library":
                ctx.storage.download_to_local(_cached[1], json_path)
            detail = "already extracted" if _cached[0] == "workspace" else "from library"
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "ic_extraction", "substep": mpn,
                            "status": "complete", "detail": detail})
            continue

        # Need PDF — check project uploads first, then library
        pdf_path = ctx.ws.local_path(f"uploads/datasheets/{safe}.pdf")
        if not pdf_path.is_file():
            lib_ds_key = proj_svc.library_has_datasheet(ctx.storage, mpn)
            if lib_ds_key:
                ctx.storage.download_to_local(lib_ds_key, pdf_path)
            else:
                ctx.skipped.append(SkippedItem(mpn, "ic_extraction", "No datasheet uploaded"))
                broker.publish(ctx.project_id, "step_update",
                               {"stage": "ic_extraction", "substep": mpn,
                                "status": "failed", "error": "No datasheet uploaded"})
                continue
        pending.append((mpn, safe, json_path, pdf_path))

    # Phase 2 (concurrent, up to ic_concurrency): extract cache-miss MPNs.
    sem = asyncio.Semaphore(settings.ic_concurrency)

    async def _extract_one(mpn: str, safe: str, json_path: Path, pdf_path: Path) -> None:
        async with sem:
            # Soft gate: once the balance is exhausted, don't *start* new ICs.
            # The first unit to trip sets ctx.paused; later units that acquire
            # the semaphore bail here, while in-flight units finish + charge.
            if ctx.paused:
                return
            if not _check_credit_gate(ctx, "ic_extraction", mpn,
                                      estimate_stage_cost_usd("ic_extraction")):
                await _paused_stage_publish(ctx, "ic_extraction", "out of credits")
                return

            # Private logger so concurrent extractions don't interleave their
            # API entries — charging slices exactly this IC's calls.
            private = ApiLogger(free=ctx.api_logger.free)
            try:
                broker.publish(ctx.project_id, "step_update",
                               {"stage": "ic_extraction", "substep": mpn,
                                "status": "running", "detail": "extracting pintable"})

                await extraction.extract_pintable(
                    mpn, str(pdf_path), extracted_dir,
                    taxonomy_dir=ctx.ws.taxonomy_dir,
                    api_logger=private,
                )

                # Upload to storage, then copy to library
                extracted_key = f"{ctx.ws.prefix}/extracted/{safe}.json"
                ctx.storage.upload_from_local(json_path, extracted_key)
                proj_svc.save_to_library(ctx.storage, extracted_key, "extracted", f"{safe}.json")

                # Upload source datasheet PDF to library (content-addressed)
                store_datasheet(ctx.storage, pdf_path, mpn)

                # Merge this IC's API entries into the shared log and charge —
                # post-execution so a crash before the save above would not
                # have charged the user.
                _charge_private_logger(ctx, private)
                ctx.pause_last_completed = f"Extracted {mpn}"

                broker.publish(ctx.project_id, "step_update",
                               {"stage": "ic_extraction", "substep": mpn,
                                "status": "complete"})

            except CancelRequested:
                # Cancel aborts the whole run. Preserve billing data for any
                # completed calls, then propagate so gather surfaces it.
                if private.entries:
                    ctx.api_logger.entries.extend(private.entries)
                raise
            except Exception as e:
                # Per-IC isolation. Preserve billing data for any calls that
                # did complete (logged but, as before, not charged on failure).
                if private.entries:
                    ctx.api_logger.entries.extend(private.entries)
                ctx.skipped.append(SkippedItem(mpn, "ic_extraction", str(e)))
                broker.publish(ctx.project_id, "step_update",
                               {"stage": "ic_extraction", "substep": mpn,
                                "status": "failed", "error": str(e)})

    results = await asyncio.gather(
        *(_extract_one(mpn, safe, json_path, pdf_path)
          for mpn, safe, json_path, pdf_path in pending),
        return_exceptions=True,
    )
    # Surface cancellation so the top-level run handler cleans up. Per-IC
    # failures stay isolated (already captured as skipped components above).
    for r in results:
        if isinstance(r, (asyncio.CancelledError, CancelRequested)):
            raise r

    broker.publish(ctx.project_id, "step_update",
                   {"stage": "ic_extraction", "status": "complete"})


async def _stage_simple_extraction(ctx: PipelineContext) -> None:
    """Stage 2.5 — Extract specs for discrete/simple components."""
    if not ctx.simple_mpns:
        return

    models_dir = ctx.ws.local_path("models")

    _simple_cache: dict[str, tuple] = {}
    _simple_new_count = 0
    for mpn in ctx.simple_mpns:
        safe = safe_mpn(mpn)
        model_path = models_dir / f"{safe}.json"
        if model_path.is_file():
            _simple_cache[mpn] = ("workspace",)
            continue
        lib_key = proj_svc.library_has_model(ctx.storage, mpn)
        if lib_key:
            _simple_cache[mpn] = ("library", lib_key)
            continue
        _simple_new_count += 1

    broker.publish(ctx.project_id, "step_update",
                   {"stage": "simple_extraction", "status": "running",
                    "total_new": _simple_new_count})

    for mpn, refs in ctx.simple_mpns.items():
        safe = safe_mpn(mpn)
        model_path = models_dir / f"{safe}.json"

        _cached = _simple_cache.get(mpn)
        if _cached:
            if _cached[0] == "library":
                ctx.storage.download_to_local(_cached[1], model_path)
            detail = "already extracted" if _cached[0] == "workspace" else "from library"
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "simple_extraction", "substep": mpn,
                            "status": "complete", "detail": detail})
            continue

        # Check for uploaded PDF — check project uploads first, then library
        pdf_path = ctx.ws.local_path(f"uploads/datasheets/{safe}.pdf")
        if not pdf_path.is_file():
            lib_ds_key = proj_svc.library_has_datasheet(ctx.storage, mpn)
            if lib_ds_key:
                ctx.storage.download_to_local(lib_ds_key, pdf_path)
            else:
                broker.publish(ctx.project_id, "step_update",
                               {"stage": "simple_extraction", "substep": mpn,
                                "status": "complete", "detail": "no datasheet (optional)"})
                continue

        if not _check_credit_gate(ctx, "simple_extraction", mpn, estimate_stage_cost_usd("simple_extraction")):
            await _paused_stage_publish(ctx, "simple_extraction", "out of credits")
            return

        try:
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "simple_extraction", "substep": mpn,
                            "status": "running", "detail": "extracting specs"})

            before_count = len(ctx.api_logger.entries)
            comp_type = ctx.simple_mpn_types[mpn]
            await extraction.extract_specs(
                mpn, str(pdf_path), comp_type, models_dir,
                taxonomy_dir=ctx.ws.taxonomy_dir,
                api_logger=ctx.api_logger,
            )

            # Upload to storage, then copy to library
            model_key = f"{ctx.ws.prefix}/models/{safe}.json"
            ctx.storage.upload_from_local(model_path, model_key)
            proj_svc.save_to_library(ctx.storage, model_key, "models", f"{safe}.json")

            # Upload source datasheet PDF to library (content-addressed)
            store_datasheet(ctx.storage, pdf_path, mpn)

            _charge_for_logs(ctx, before_count)
            ctx.pause_last_completed = f"Extracted {mpn}"

            broker.publish(ctx.project_id, "step_update",
                           {"stage": "simple_extraction", "substep": mpn,
                            "status": "complete"})

        except Exception as e:
            ctx.skipped.append(SkippedItem(mpn, "simple_extraction", str(e)))
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "simple_extraction", "substep": mpn,
                            "status": "failed", "error": str(e)})

    # DigiKey fallback for simple components without datasheets
    if settings.use_digikey:
        no_datasheet_mpns = [
            mpn for mpn in ctx.simple_mpns
            if mpn not in _simple_cache
            and not (models_dir / f"{safe_mpn(mpn)}.json").is_file()
        ]
        if no_datasheet_mpns:
            from backend.services.digikey import fetch_params

            for mpn in no_datasheet_mpns:
                safe = safe_mpn(mpn)
                model_path = models_dir / f"{safe}.json"

                try:
                    # Check library first (may have been added during this run)
                    lib_key = proj_svc.library_has_model(ctx.storage, mpn)
                    if lib_key:
                        ctx.storage.download_to_local(lib_key, model_path)
                        broker.publish(ctx.project_id, "step_update",
                                       {"stage": "simple_extraction", "substep": mpn,
                                        "status": "complete", "detail": "specs from library"})
                        continue

                    broker.publish(ctx.project_id, "step_update",
                                   {"stage": "simple_extraction", "substep": mpn,
                                    "status": "running", "detail": "auto-resolving via DigiKey"})

                    result = await fetch_params(mpn)
                    if not result.ok or not result.params:
                        raise RuntimeError(result.error or "No DigiKey parameters")

                    comp_type = ctx.simple_mpn_types[mpn]
                    model = await extraction.auto_resolve_specs(
                        mpn=mpn,
                        digikey_params=result.params.parameters,
                        digikey_category=result.params.category,
                        digikey_description=result.params.description,
                        component_type=comp_type,
                        taxonomy_dir=ctx.ws.taxonomy_dir,
                        api_logger=ctx.api_logger,
                    )

                    model_path.write_text(model.model_dump_json(indent=2) + "\n")

                    # Upload to storage + library
                    model_key = f"{ctx.ws.prefix}/models/{safe}.json"
                    ctx.storage.upload_from_local(model_path, model_key)
                    proj_svc.save_to_library(
                        ctx.storage, model_key, "models", f"{safe}.json",
                    )

                    broker.publish(ctx.project_id, "step_update",
                                   {"stage": "simple_extraction", "substep": mpn,
                                    "status": "complete", "detail": "auto-resolved via DigiKey"})

                except Exception as e:
                    ctx.skipped.append(SkippedItem(
                        mpn, "simple_digikey_resolve", str(e),
                    ))
                    broker.publish(ctx.project_id, "step_update",
                                   {"stage": "simple_extraction", "substep": mpn,
                                    "status": "failed", "error": str(e)})

    broker.publish(ctx.project_id, "step_update",
                   {"stage": "simple_extraction", "status": "complete"})


async def _stage_passive_extraction(ctx: PipelineContext) -> None:
    """Stage 3 — Extract passive patterns; DigiKey fallback for unresolved."""
    broker.publish(ctx.project_id, "step_update",
                   {"stage": "passive_extraction", "status": "running"})

    patterns_dir = ctx.ws.local_path("patterns")
    models_dir = ctx.ws.local_path("models")

    # Seed project patterns from library
    lib_pattern_keys = proj_svc.list_library_patterns(ctx.storage)
    for lib_key in lib_pattern_keys:
        filename = lib_key.rsplit("/", 1)[-1]
        dest = patterns_dir / filename
        if not dest.exists():
            ctx.storage.download_to_local(lib_key, dest)

    ctx.patterns = load_patterns(str(patterns_dir)) if patterns_dir.is_dir() else []

    unresolved: dict[str, list[str]] = {}
    for mpn, refs in ctx.passive_mpns.items():
        if resolve_mpn(mpn, ctx.patterns) is not None:
            continue
        # Check if specs already extracted in a previous run
        safe = safe_mpn(mpn)
        # Per-project model already on disk (e.g. the wizard's
        # /lcsc/resolve-passive endpoint resolved it before the pipeline
        # ran). Trust it — no re-charge, no re-extraction.
        if (models_dir / f"{safe}.json").is_file():
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "passive_extraction",
                            "substep": mpn, "status": "complete",
                            "detail": "specs already resolved"})
            continue
        lib_model_key = proj_svc.library_has_passive_model(ctx.storage, mpn)
        if lib_model_key:
            dest = models_dir / f"{safe}.json"
            if not dest.is_file():
                ctx.storage.download_to_local(lib_model_key, dest)
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "passive_extraction",
                            "substep": mpn, "status": "complete",
                            "detail": "specs from library"})
            continue
        unresolved[mpn] = refs

    if not unresolved:
        broker.publish(ctx.project_id, "step_update",
                       {"stage": "passive_extraction", "status": "complete",
                        "detail": "all passives already resolved"})
        return

    ds_dir = ctx.ws.local_path("uploads/datasheets")

    # Collect unique datasheets: deduplicate by library source key
    # AND by content hash so the same PDF isn't extracted multiple
    # times for cousin MPNs stored under different keys.
    _seen_lib_keys: set[str] = set()
    _seen_hashes: set[str] = set()
    passive_pdfs = []
    for mpn in unresolved:
        safe = safe_mpn(mpn)
        pdf = ds_dir / f"{safe}.pdf"
        if not pdf.is_file():
            # Check library for datasheet
            lib_ds_key = proj_svc.library_has_datasheet(ctx.storage, mpn, patterns=ctx.patterns)
            if lib_ds_key:
                if lib_ds_key in _seen_lib_keys:
                    continue  # Same datasheet already queued for another MPN
                _seen_lib_keys.add(lib_ds_key)
                ctx.storage.download_to_local(lib_ds_key, pdf)
        if pdf.is_file():
            h = compute_md5_from_path(pdf)
            if h in _seen_hashes:
                continue  # Duplicate content already queued
            _seen_hashes.add(h)
            passive_pdfs.append(pdf)

    broker.publish(ctx.project_id, "step_update",
                   {"stage": "passive_extraction", "status": "running",
                    "total_new": len(passive_pdfs)})

    # Build set of datasheet blobs that already have a pattern
    # so we don't re-extract from a PDF that was already processed.
    _extracted_ds_keys: set[str] = set()
    for pat in ctx.patterns:
        dk = getattr(pat, "datasheet_key", None) or ""
        if dk:
            _extracted_ds_keys.add(dk)

    for pdf_path in passive_pdfs:
        # Skip if all unresolved MPNs are now covered
        if not unresolved:
            break

        # Skip if this MPN was already resolved by a previously extracted pattern
        _safe_unresolved = {safe_mpn(m) for m in unresolved}
        if pdf_path.stem not in _safe_unresolved:
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "passive_extraction",
                            "substep": pdf_path.stem,
                            "status": "complete", "detail": "resolved by pattern"})
            continue

        # Skip if a pattern was already extracted from this exact
        # PDF content (in this run or a previous one) — re-extracting
        # would produce the same regex that already failed to match.
        _pdf_hash = compute_md5_from_path(pdf_path)
        _pdf_blob_key = f"library/datasheets/blobs/{_pdf_hash}.pdf"
        if _pdf_blob_key in _extracted_ds_keys:
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "passive_extraction",
                            "substep": pdf_path.stem,
                            "status": "complete",
                            "detail": "pattern already extracted from this PDF"})
            continue

        # Resolve the safe filename back to the original MPN
        _trigger_mpn = next(
            (m for m in unresolved if safe_mpn(m) == pdf_path.stem),
            None,
        )

        if not _check_credit_gate(ctx, "passive_extraction", pdf_path.stem,
                                   estimate_stage_cost_usd("passive_pattern")):
            await _paused_stage_publish(ctx, "passive_extraction", "out of credits")
            return

        try:
            mpn_list = list(unresolved.keys())
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "passive_extraction",
                            "substep": pdf_path.stem,
                            "status": "running",
                            "detail": "extracting pattern"})

            before_count = len(ctx.api_logger.entries)
            out = await extraction.extract_pattern(
                str(pdf_path), mpn_list, patterns_dir,
                trigger_mpn=_trigger_mpn,
                taxonomy_dir=ctx.ws.taxonomy_dir,
                api_logger=ctx.api_logger,
            )

            if out:
                # Upload source datasheet PDF to library (content-addressed)
                blob_k = store_datasheet(ctx.storage, pdf_path, out.stem)
                _extracted_ds_keys.add(blob_k)

                # Write datasheet_key into pattern JSON
                pattern_data = json.loads(out.read_text())
                pattern_data["datasheet_key"] = blob_k
                out.write_text(json.dumps(pattern_data, indent=2) + "\n")

                # Upload pattern to storage, then copy to library
                rel = out.relative_to(ctx.ws.local_dir)
                pattern_key = f"{ctx.ws.prefix}/{rel}"
                ctx.storage.upload_from_local(out, pattern_key)
                proj_svc.save_to_library(ctx.storage, pattern_key, "patterns", out.name)

            # Reload and recheck
            ctx.patterns = load_patterns(str(patterns_dir))
            _prev_count = len(unresolved)
            still = {m: r for m, r in unresolved.items()
                     if resolve_mpn(m, ctx.patterns) is None}
            _newly_resolved = _prev_count - len(still)
            unresolved = still

            _charge_for_logs(ctx, before_count)
            ctx.pause_last_completed = f"Pattern from {pdf_path.stem}"

            broker.publish(ctx.project_id, "step_update",
                           {"stage": "passive_extraction",
                            "substep": pdf_path.stem,
                            "status": "complete",
                            "detail": f"pattern extracted, resolved {_newly_resolved} MPNs" if out else "pattern failed, MPN falls to DigiKey"})

        except Exception as e:
            ctx.skipped.append(SkippedItem(pdf_path.stem, "passive_extraction", str(e)))
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "passive_extraction",
                            "substep": pdf_path.stem,
                            "status": "failed", "error": str(e)})

    # Fallback for still-unresolved passives: DigiKey exact-MPN lookup first,
    # then a Haiku-powered value resolver for BOMs where the MPN column holds a
    # value token (e.g. "10uF"). Value-based results are per-project only and
    # are never written to the shared library.
    if unresolved:
        still_unresolved = [
            mpn for mpn in unresolved
            if not (models_dir / f"{safe_mpn(mpn)}.json").is_file()
        ]
        if still_unresolved:
            from backend.services.digikey import fetch_params

            # By-MPN backstop: passives with no cached LCSC payload (real-MPN
            # BOMs not pre-resolved at upload/wizard time, or wizard gaps like
            # skipped / errored / out-of-credits) get a reverse catalogue lookup
            # so the LCSC branch below can fire for them too. Fail-soft — misses
            # simply fall through to the DigiKey path.
            _need_lcsc = [m for m in still_unresolved if m not in ctx.lcsc_data]
            if _need_lcsc and settings.use_purple_parts:
                try:
                    from backend.services.purple_parts import lookup_mpn_batch
                    _parts = await lookup_mpn_batch(_need_lcsc)
                    for _m, _part in _parts.items():
                        if _part and _part.get("description"):
                            ctx.lcsc_data.setdefault(_m, _part)
                except Exception:
                    logger.warning("purple-parts by-mpn backstop failed", exc_info=True)

            # Resolve each passive concurrently (bounded by ic_concurrency),
            # mirroring _stage_ic_extraction: each in-flight unit uses a private
            # ApiLogger so concurrent API entries don't interleave, and
            # _charge_private_logger bills exactly that unit's calls atomically.
            sem = asyncio.Semaphore(settings.ic_concurrency)

            async def _resolve_one(mpn: str) -> None:
                async with sem:
                    # Soft gate: once the balance is exhausted, don't *start* new
                    # units; in-flight ones finish + charge (bounded overdraft).
                    if ctx.paused:
                        return
                    safe = safe_mpn(mpn)
                    model_path = models_dir / f"{safe}.json"

                    # Check library first — free, no charge, no gate.
                    lib_key = proj_svc.library_has_passive_model(ctx.storage, mpn)
                    if lib_key:
                        ctx.storage.download_to_local(lib_key, model_path)
                        broker.publish(ctx.project_id, "step_update",
                                       {"stage": "passive_extraction",
                                        "substep": mpn, "status": "complete",
                                        "detail": "specs from library"})
                        return

                    if not _check_credit_gate(ctx, "passive_extraction", mpn,
                                              estimate_stage_cost_usd("digikey_resolve")):
                        await _paused_stage_publish(ctx, "passive_extraction", "out of credits")
                        return

                    # Private logger so concurrent resolves don't interleave their
                    # API entries — charging slices exactly this passive's calls.
                    private = ApiLogger(free=ctx.api_logger.free)
                    model = None
                    resolved_via: str | None = None
                    first_error: str | None = None
                    try:
                        # --- LCSC (purple-parts) first: the description carries
                        # value/voltage/dielectric/tolerance/package for typical
                        # passives. Synthesize a DigiKey-shaped payload and reuse
                        # auto_resolve_specs.
                        lcsc = ctx.lcsc_data.get(mpn)
                        if lcsc and lcsc.get("description"):
                            try:
                                broker.publish(ctx.project_id, "step_update",
                                               {"stage": "passive_extraction",
                                                "substep": mpn, "status": "running",
                                                "detail": "auto-resolving via LCSC"})
                                synth_category = " / ".join(
                                    p for p in (lcsc.get("category"), lcsc.get("subcategory")) if p
                                )
                                synth_params: dict[str, str] = {}
                                if lcsc.get("package"):
                                    synth_params["Package / Case"] = lcsc["package"]
                                if lcsc.get("manufacturer"):
                                    synth_params["Manufacturer"] = lcsc["manufacturer"]
                                model = await extraction.auto_resolve_specs(
                                    mpn=mpn,
                                    digikey_params=synth_params,
                                    digikey_category=synth_category or None,
                                    digikey_description=lcsc["description"],
                                    component_type="passive",
                                    taxonomy_dir=ctx.ws.taxonomy_dir,
                                    api_logger=private,
                                )
                                if model is not None:
                                    resolved_via = "lcsc"
                            except Exception as e:
                                first_error = str(e)

                        # --- DigiKey: only trusted on an exact MPN hit ----------
                        if model is None and settings.use_digikey:
                            try:
                                broker.publish(ctx.project_id, "step_update",
                                               {"stage": "passive_extraction",
                                                "substep": mpn, "status": "running",
                                                "detail": "auto-resolving via DigiKey"})
                                result = await fetch_params(mpn)
                                if result.ok and result.params:
                                    model = await extraction.auto_resolve_specs(
                                        mpn=mpn,
                                        digikey_params=result.params.parameters,
                                        digikey_category=result.params.category,
                                        digikey_description=result.params.description,
                                        component_type="passive",
                                        taxonomy_dir=ctx.ws.taxonomy_dir,
                                        api_logger=private,
                                    )
                                    resolved_via = "digikey"
                                else:
                                    first_error = result.error or "no DigiKey parameters"
                            except Exception as e:
                                first_error = str(e)

                        # --- Value fallback: parse the BOM value via Haiku ------
                        if model is None:
                            bom_value = ctx.passive_values.get(mpn, "").strip()
                            refs = ctx.passive_mpns.get(mpn, [])
                            pref_match = re.match(r"^[A-Za-z]+", refs[0]) if refs else None
                            ref_prefix = pref_match.group(0).upper() if pref_match else ""
                            if bom_value and ref_prefix in {"C", "R", "L", "FB"}:
                                try:
                                    broker.publish(ctx.project_id, "step_update",
                                                   {"stage": "passive_extraction",
                                                    "substep": mpn, "status": "running",
                                                    "detail": f"resolving from BOM value {bom_value!r}"})
                                    model = await extraction.resolve_from_value(
                                        mpn=mpn, value=bom_value,
                                        ref_prefix=ref_prefix,
                                        component_type="passive",
                                        taxonomy_dir=ctx.ws.taxonomy_dir,
                                        api_logger=private,
                                    )
                                    resolved_via = "value"
                                except Exception as e:
                                    first_error = first_error or str(e)

                        if model is None:
                            err = first_error or "no LCSC/DigiKey hit and no usable BOM value"
                            # Preserve any logged-but-failed calls (not charged).
                            if private.entries:
                                ctx.api_logger.entries.extend(private.entries)
                            ctx.skipped.append(SkippedItem(mpn, "passive_resolve", err))
                            broker.publish(ctx.project_id, "step_update",
                                           {"stage": "passive_extraction",
                                            "substep": mpn, "status": "failed",
                                            "error": err})
                            return

                        model_path.write_text(model.model_dump_json(indent=2) + "\n")

                        # Always upload to the project's own storage so graph build picks it up
                        model_key = f"{ctx.ws.prefix}/models/{safe}.json"
                        ctx.storage.upload_from_local(model_path, model_key)

                        # Share to the global library when the resolution is backed
                        # by a real MPN (DigiKey or LCSC). Value-based tokens like
                        # "10uF" are not real MPNs and would poison lookups for
                        # every future project.
                        if resolved_via in ("digikey", "lcsc"):
                            proj_svc.save_to_library(
                                ctx.storage, model_key, "passives", f"{safe}.json",
                            )

                        # Merge this passive's API entries into the shared log and
                        # charge — post-save so a crash before the writes above
                        # would not have charged the user.
                        _charge_private_logger(ctx, private)
                        ctx.pause_last_completed = f"Resolved {mpn}"

                        detail = {
                            "lcsc": "auto-resolved via LCSC",
                            "digikey": "auto-resolved via DigiKey",
                            "value": "resolved from BOM value (not saved to library)",
                        }.get(resolved_via, "resolved")
                        broker.publish(ctx.project_id, "step_update",
                                       {"stage": "passive_extraction",
                                        "substep": mpn, "status": "complete",
                                        "detail": detail})

                    except CancelRequested:
                        # Cancel aborts the whole run. Preserve billing data for
                        # any completed calls, then propagate so gather surfaces it.
                        if private.entries:
                            ctx.api_logger.entries.extend(private.entries)
                        raise
                    except Exception as e:
                        # Per-passive isolation. Preserve billing data for any
                        # calls that did complete (logged but not charged on failure).
                        if private.entries:
                            ctx.api_logger.entries.extend(private.entries)
                        ctx.skipped.append(SkippedItem(mpn, "passive_resolve", str(e)))
                        broker.publish(ctx.project_id, "step_update",
                                       {"stage": "passive_extraction",
                                        "substep": mpn, "status": "failed",
                                        "error": str(e)})

            results = await asyncio.gather(
                *(_resolve_one(m) for m in still_unresolved),
                return_exceptions=True,
            )
            # Surface cancellation so the top-level run handler cleans up. Per-
            # passive failures stay isolated (captured as skipped above).
            for r in results:
                if isinstance(r, (asyncio.CancelledError, CancelRequested)):
                    raise r

    broker.publish(ctx.project_id, "step_update",
                   {"stage": "passive_extraction", "status": "complete"})


async def _stage_graph_build(ctx: PipelineContext) -> None:
    """Stage 4 — Build the design graph from netlist, BOM, and extracted data."""
    broker.publish(ctx.project_id, "step_update",
                   {"stage": "graph_build", "status": "running"})

    bom_path = ctx.ws.local_path("uploads/bom.csv")
    netlist_path = ctx.ws.netlist_local_path()
    extracted_dir = ctx.ws.local_path("extracted")
    patterns_dir = ctx.ws.local_path("patterns")
    models_dir = ctx.ws.local_path("models")

    ctx.graph = build_graph(
        str(netlist_path),
        str(bom_path),
        str(extracted_dir),
        str(patterns_dir),
        str(models_dir),
        reference_col=ctx.ref_col,
        mpn_col=ctx.mpn_col,
        skipped=ctx.skipped,
        include_subdesigns=(
            set(ctx.meta.netlist_subdesigns)
            if ctx.meta.netlist_subdesigns is not None
            else None
        ),
    )

    graph_path = ctx.ws.local_path("design_graph.json")
    graph_path.write_text(ctx.graph.model_dump_json(indent=2) + "\n")

    broker.publish(ctx.project_id, "step_update",
                   {"stage": "graph_build", "status": "complete",
                    "detail": f"{len(ctx.graph.components)} components, {len(ctx.graph.nets)} nets"})


async def _stage_validation(ctx: PipelineContext) -> None:
    """Stage 6 — BOM summary, derating, then per-IC direct datasheet review.

    BOM summary and derating are quick deterministic steps that run first;
    they're not separate UI steps but they depend on the graph being ready.
    """
    ds_dir = ctx.ws.local_path("uploads/datasheets")
    extracted_dir = ctx.ws.local_path("extracted")
    graph_path = ctx.ws.local_path("design_graph.json")

    # BOM summary (collate — no AI, no SSE event)
    ds_mpns: set[str] = set()
    if ds_dir.is_dir():
        for pdf in ds_dir.glob("*.pdf"):
            ds_mpns.add(pdf.stem)
    # Also include datasheets available in the global library
    # (covers resolved MPNs whose PDFs weren't downloaded to workspace)
    for comp in ctx.graph.components.values():
        if comp.mpn and comp.mpn not in ds_mpns:
            if proj_svc.library_has_datasheet(ctx.storage, comp.mpn, patterns=ctx.patterns):
                ds_mpns.add(comp.mpn)
    descriptions = _ic_descriptions(extracted_dir)
    bom_rows = build_bom_summary(
        ctx.graph, datasheet_mpns=ds_mpns, descriptions=descriptions,
    )
    bom_summary_path = ctx.ws.local_path("bom_summary.json")
    bom_summary_path.write_text(json.dumps(bom_rows, indent=2) + "\n")

    # Capacitor voltage derating (no AI, no SSE event)
    derating_rows = build_derating_table(ctx.graph)
    derating_path = ctx.ws.local_path("derating.json")
    derating_path.write_text(json.dumps(derating_rows, indent=2) + "\n")

    # Ensure all IC datasheet PDFs are available locally for review.
    # Cached ICs skipped pintable extraction, so their PDFs may not
    # have been downloaded yet.
    for mpn in ctx.ic_mpns:
        safe = safe_mpn(mpn)
        pdf_path = ds_dir / f"{safe}.pdf"
        if not pdf_path.is_file():
            lib_ds_key = proj_svc.library_has_datasheet(ctx.storage, mpn)
            if lib_ds_key:
                ctx.storage.download_to_local(lib_ds_key, pdf_path)

    # Snapshot the full review queue so pause checkpoints can show what's left.
    # Mirrors the filter in validate_design_async: ICs with a PDF available.
    planned_refs: list[str] = []
    for ref, comp in ctx.graph.components.items():
        if comp.component_type != ComponentType.IC:
            continue
        mpn = comp.mpn or comp.value
        if not mpn:
            continue
        if (ds_dir / f"{safe_mpn(mpn)}.pdf").is_file():
            planned_refs.append(ref)
    ctx.all_review_refs = sorted(planned_refs, key=natural_sort_key)

    broker.publish(ctx.project_id, "step_update",
                   {"stage": "validation", "status": "running"})

    report_path = ctx.ws.local_path("report.json")

    async def on_validation_progress(ref: str, turn: int, tool: str, detail: str):
        if tool == "error":
            broker.publish(ctx.project_id, "step_update",
                           {"stage": "validation", "substep": ref,
                            "status": "failed", "detail": detail})
            return
        is_done = tool in ("submit_review", "skipped")
        broker.publish(ctx.project_id, "step_update",
                       {"stage": "validation", "substep": ref,
                        "status": "complete" if is_done else "running",
                        "detail": detail if is_done else tool})

    async def on_ic_error(ref: str, exc: BaseException) -> None:
        ctx.skipped.append(SkippedItem(
            ref, "validation", f"{type(exc).__name__}: {exc}",
        ))

    async def before_ic(ref: str) -> bool:
        # Per-IC credit gate — review cost is the biggest single unit.
        # Include the post-review normalize pass so we don't run out of
        # margin between the two halves of a single IC's work.
        ic_cost = estimate_stage_cost_usd("review")
        if settings.normalize_findings_enabled:
            ic_cost += estimate_stage_cost_usd("normalize")
        if not _check_credit_gate(ctx, "validation", ref, ic_cost):
            await _paused_stage_publish(ctx, "validation", "out of credits")
            return False
        return True

    async def on_ic_done(ref: str, result: Any, private: ApiLogger | None = None) -> None:
        # Charge for exactly this IC's API calls (its private logger), merging
        # them into the shared log. Concurrency-safe: the charge slice can't
        # pick up another in-flight IC's entries.
        if private is not None:
            _charge_private_logger(ctx, private)
        ctx.completed_review_refs.add(ref)
        ctx.pause_last_completed = f"Reviewed {ref}"

    async def on_dedupe_done(private: ApiLogger | None = None) -> None:
        # The cross-IC dedup is a single end-of-run LLM call; charge it like a
        # per-IC unit. Post-charge (no pre-gate): by the time all ICs are
        # reviewed the run isn't paused, and one Haiku-class call is within the
        # bounded-overdraft tolerance already used for in-flight units.
        if private is not None:
            _charge_private_logger(ctx, private)

    # Resume-aware: skip ICs that were already reviewed in a previous pass
    ctx.report = await validate_design_async(
        str(graph_path),
        str(report_path),
        str(extracted_dir),
        pdf_dir=str(ds_dir),
        on_progress=on_validation_progress,
        api_logger=ctx.api_logger,
        storage=ctx.storage,
        skip_refs=set(ctx.completed_review_refs),
        before_ic=before_ic,
        on_ic_done=on_ic_done,
        on_ic_error=on_ic_error,
        on_dedupe_done=on_dedupe_done,
        project_prefix=proj_svc.project_prefix(ctx.user_id, ctx.project_id),
        run_meta={"git_commit": _git_commit()},
    )

    if ctx.paused:
        return

    broker.publish(ctx.project_id, "step_update",
                   {"stage": "validation", "status": "complete"})


# ---------------------------------------------------------------------------
# Stage registry — reorder entries here to change pipeline execution order
# ---------------------------------------------------------------------------


@dataclass
class StageSpec:
    """Metadata + function reference for a single pipeline stage."""
    stage_id: str
    title: str
    fn: Callable[[PipelineContext], Awaitable[None]]


PIPELINE_STAGES: list[StageSpec] = [
    StageSpec("bom_parse",          "Parse BOM",                    _stage_bom_parse),
    StageSpec("ic_extraction",      "IC Datasheet Extraction",      _stage_ic_extraction),
    StageSpec("simple_extraction",  "Component Specs Extraction",   _stage_simple_extraction),
    StageSpec("passive_extraction", "Passive Pattern Extraction",   _stage_passive_extraction),
    StageSpec("graph_build",        "Build Design Graph",           _stage_graph_build),
    StageSpec("validation",         "Review Design",                _stage_validation),
]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def run_pipeline(
    storage: StorageBackend, user_id: str, project_id: str,
    *,
    resume: bool = False,
    free: bool = False,
) -> None:
    """Run the full pipeline for a project.

    Iterates through ``PIPELINE_STAGES`` in order.  If a stage sets
    ``ctx.paused = True`` (credit gate tripped), the loop exits early and
    the project is left in ``paused_insufficient_credits`` with a
    checkpoint so it can be resumed later.

    When ``resume=True``, prior completed review refs and spent credits are
    restored from the project's ``pause_checkpoint`` so completed work is
    skipped on the next pass.

    When ``free=True`` (admin-initiated rerun), every call runs through
    ``ApiLogger(free=True)`` so ``credits_charged`` is zeroed, the credit
    gate is bypassed, and ``meta.total_cost_usd`` is preserved rather than
    incremented.  The raw Anthropic cost is still captured in log entries.
    """
    try:
        meta = proj_svc.get_project(storage, user_id, project_id)
        if not meta:
            raise ValueError(f"Project {project_id} not found")

        bom_key = proj_svc.get_bom_key(storage, user_id, project_id)
        netlist_key = proj_svc.get_netlist_key(storage, user_id, project_id)

        if not bom_key or not netlist_key:
            proj_svc.update_project(storage, user_id, project_id, status="error",
                                    pipeline_state={"error": "Missing BOM or netlist"})
            broker.publish(project_id, "pipeline_error",
                           {"error": "Missing BOM or netlist"})
            return

        api_logger = ApiLogger(free=free)

        # Worker boot transition: queued → running, gen-match enforced so
        # two concurrent worker boots can't both progress past this line.
        # Tolerate already-running for resume from a previously-killed
        # worker (rare, but safe).
        try:
            proj_svc.transition_status(
                storage, user_id, project_id,
                from_status={proj_svc.STATUS_QUEUED, proj_svc.STATUS_RUNNING},
                to_status=proj_svc.STATUS_RUNNING,
                pause_checkpoint=None, pause_reason=None,
                cancel_requested=False,
            )
        except proj_svc.StatusConflict:
            # Project moved to a terminal state (cancelled/error/complete)
            # before this worker booted — nothing more to do.
            logger.warning("worker booted into non-queued project %s; exiting", project_id)
            return

        async with PipelineWorkspace(storage, user_id, project_id) as ws:
            min_ver = settings_svc.get_min_model_version(storage)
            ctx = PipelineContext(
                storage=storage,
                user_id=user_id,
                project_id=project_id,
                ws=ws,
                api_logger=api_logger,
                meta=meta,
                min_ver=min_ver,
                free=free,
            )

            # On resume: carry over prior per-IC review completion so
            # validate_design_async skips ICs we've already paid for.
            if resume and meta.completed_review_refs:
                ctx.completed_review_refs = set(meta.completed_review_refs)
            ctx.credits_spent = float(meta.credits_spent or 0)

            for spec in PIPELINE_STAGES:
                await spec.fn(ctx)
                # Flush api logs at every stage boundary so a preempted
                # worker (Cloud Run scale-in, OOM, manual cancel between
                # stages) doesn't lose billing data.
                try:
                    api_logger.flush(storage, user_id, project_id)
                except Exception:
                    logger.exception("api_logs flush failed at stage boundary")
                if ctx.paused:
                    break

            # Write API call logs to project storage regardless of state
            log_jsonl = api_logger.to_jsonl()
            if log_jsonl:
                log_path = ws.local_path("api_logs.jsonl")
                log_path.write_text(log_jsonl)

        # --- PipelineWorkspace exit uploads results ---

        skipped_dicts = [s.to_dict() for s in ctx.skipped] if ctx.skipped else None
        # Free admin reruns preserve prior spend: the Anthropic cost is
        # still real, but it shouldn't surface as user-borne cost.
        if ctx.free:
            project_cost = float(meta.total_cost_usd or 0)
        else:
            project_cost = total_cost(api_logger.entries) + float(meta.total_cost_usd or 0)

        if ctx.paused:
            pending_refs = [
                r for r in ctx.all_review_refs
                if r not in ctx.completed_review_refs
            ]
            checkpoint = {
                "paused_at": ctx.pause_unit_id,
                "paused_stage": ctx.pause_stage,
                "last_completed_label": ctx.pause_last_completed,
                "completed_review_refs": sorted(ctx.completed_review_refs, key=natural_sort_key),
                "pending_review_refs": pending_refs,
            }
            proj_svc.update_project(
                storage, user_id, project_id,
                status="paused_insufficient_credits",
                skipped_components=skipped_dicts or None,
                total_cost_usd=project_cost,
                credits_spent=ctx.credits_spent,
                pause_checkpoint=checkpoint,
                pause_reason="insufficient_credits",
                completed_review_refs=sorted(ctx.completed_review_refs, key=natural_sort_key),
            )
            broker.publish(project_id, "pipeline_paused",
                           {"reason": "insufficient_credits",
                            "last_completed": ctx.pause_last_completed,
                            "stage": ctx.pause_stage,
                            "unit_id": ctx.pause_unit_id,
                            "completed_review_refs": sorted(ctx.completed_review_refs, key=natural_sort_key),
                            "pending_review_refs": pending_refs})

            # Fire-and-forget paused email
            from backend.services.email import send_pipeline_paused_email
            from backend.services.cost_estimator import estimate_pipeline_cost
            try:
                balance = get_billing().get_balance(storage, user_id)
                # Re-estimate against current library state so the email
                # shows remaining work, not the original pre-run total.
                needed_low = 0.0
                try:
                    remaining = estimate_pipeline_cost(storage, user_id, project_id)
                    needed_low = max(0.0, remaining.credits_low - max(0.0, balance))
                except Exception:
                    pass
                await send_pipeline_paused_email(
                    user_id=user_id,
                    project_name=meta.name,
                    project_id=project_id,
                    last_completed=ctx.pause_last_completed,
                    stage=ctx.pause_stage,
                    balance=balance,
                    credits_needed_low=needed_low,
                )
            except Exception:
                pass
            return

        report_summary = ctx.report.summary if ctx.report else {}
        proj_svc.update_project(
            storage, user_id, project_id,
            status="complete",
            summary=report_summary,
            skipped_components=skipped_dicts or None,
            total_cost_usd=project_cost,
            credits_spent=ctx.credits_spent,
            pause_checkpoint=None, pause_reason=None,
            completed_review_refs=sorted(ctx.completed_review_refs),
        )

        broker.publish(project_id, "pipeline_complete",
                       {"summary": report_summary,
                        "skipped": skipped_dicts or []})

        # Send email notification (fire-and-forget)
        from backend.services.email import send_report_ready_email
        try:
            await send_report_ready_email(
                user_id=user_id,
                project_name=meta.name,
                project_id=project_id,
                summary=report_summary,
                total_cost_usd=project_cost,
            )
        except Exception:
            pass  # send_report_ready_email handles errors internally

    except (asyncio.CancelledError, CancelRequested):
        # CancelRequested fires from the cancel gate inside
        # _charge_for_logs after the user clicks Cancel.
        # asyncio.CancelledError can also arrive during local-dev
        # subprocess shutdown (SIGTERM). Both are handled the same way.
        try:
            proj_svc.transition_status(
                storage, user_id, project_id,
                from_status={proj_svc.STATUS_RUNNING, proj_svc.STATUS_QUEUED},
                to_status=proj_svc.STATUS_CANCELLED,
                pipeline_state={"error": "Pipeline cancelled by user"},
                cancel_requested=False,
            )
        except proj_svc.StatusConflict:
            pass
        broker.publish(project_id, "pipeline_cancelled", {"error": "Pipeline cancelled by user"})
        # Last-mile flush so partial billing is captured.
        try:
            api_logger.flush(storage, user_id, project_id)  # type: ignore[has-type]
        except Exception:
            pass

    except Exception as e:
        logger.exception("Pipeline run crashed for project %s", project_id)
        try:
            proj_svc.transition_status(
                storage, user_id, project_id,
                from_status={proj_svc.STATUS_RUNNING, proj_svc.STATUS_QUEUED},
                to_status=proj_svc.STATUS_ERROR,
                pipeline_state={"error": str(e)},
                cancel_requested=False,
            )
        except proj_svc.StatusConflict:
            pass
        broker.publish(project_id, "pipeline_error", {"error": str(e)})
        try:
            api_logger.flush(storage, user_id, project_id)  # type: ignore[has-type]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Regen Pipeline (graph + selected stages only)
# ---------------------------------------------------------------------------


async def run_regen_pipeline(
    storage: StorageBackend, user_id: str, project_id: str, stages: list[str]
) -> None:
    """Rebuild the design graph and regenerate only the requested stages.

    Valid stages: "derating".  Graph build always runs first.
    BOM summary is always regenerated since it depends on the graph and is cheap.
    """
    try:
        meta = proj_svc.get_project(storage, user_id, project_id)
        if not meta:
            raise ValueError(f"Project {project_id} not found")

        # Regen is admin-initiated — run in free mode so log entries record
        # `credits_charged: 0` and don't surface as user-borne cost.
        api_logger = ApiLogger(free=True)

        try:
            proj_svc.transition_status(
                storage, user_id, project_id,
                from_status={proj_svc.STATUS_QUEUED, proj_svc.STATUS_RUNNING},
                to_status=proj_svc.STATUS_RUNNING,
                cancel_requested=False,
            )
        except proj_svc.StatusConflict:
            logger.warning("regen worker booted into non-queued project %s; exiting", project_id)
            return

        async with PipelineWorkspace(storage, user_id, project_id) as ws:
            bom_path = ws.local_path("uploads/bom.csv")
            netlist_path = ws.netlist_local_path()
            extracted_dir = ws.local_path("extracted")
            patterns_dir = ws.local_path("patterns")
            models_dir = ws.local_path("models")

            col_map = meta.bom_columns or {}
            ref_col = col_map.get("reference", "Reference")
            mpn_col = col_map.get("mpn", "Manufacturer Part Number")

            # ------------------------------------------------------------------
            # Rebuild Graph (always)
            # ------------------------------------------------------------------
            broker.publish(project_id, "step_update",
                           {"stage": "graph_build", "status": "running"})

            graph = build_graph(
                str(netlist_path),
                str(bom_path),
                str(extracted_dir),
                str(patterns_dir),
                str(models_dir),
                reference_col=ref_col,
                mpn_col=mpn_col,
                include_subdesigns=(
                    set(meta.netlist_subdesigns)
                    if meta.netlist_subdesigns is not None
                    else None
                ),
            )

            graph_path = ws.local_path("design_graph.json")
            graph_path.write_text(graph.model_dump_json(indent=2) + "\n")

            broker.publish(project_id, "step_update",
                           {"stage": "graph_build", "status": "complete",
                            "detail": f"{len(graph.components)} components, {len(graph.nets)} nets"})

            # ------------------------------------------------------------------
            # BOM Summary (always — cheap, depends on graph)
            # ------------------------------------------------------------------
            patterns = load_patterns(str(patterns_dir)) if patterns_dir.is_dir() else []
            ds_dir = ws.local_path("uploads/datasheets")
            ds_mpns: set[str] = set()
            if ds_dir.is_dir():
                for pdf in ds_dir.glob("*.pdf"):
                    ds_mpns.add(pdf.stem)
            for comp in graph.components.values():
                if comp.mpn and comp.mpn not in ds_mpns:
                    if proj_svc.library_has_datasheet(storage, comp.mpn, patterns=patterns):
                        ds_mpns.add(comp.mpn)
            descriptions = _ic_descriptions(ws.local_path("extracted"))
            bom_rows = build_bom_summary(
                graph, datasheet_mpns=ds_mpns, descriptions=descriptions,
            )
            bom_summary_path = ws.local_path("bom_summary.json")
            bom_summary_path.write_text(json.dumps(bom_rows, indent=2) + "\n")

            # ------------------------------------------------------------------
            # Derating (if requested)
            # ------------------------------------------------------------------
            if "derating" in stages:
                derating_rows = build_derating_table(graph)
                derating_path = ws.local_path("derating.json")
                derating_path.write_text(json.dumps(derating_rows, indent=2) + "\n")

            # Write API call logs
            log_jsonl = api_logger.to_jsonl()
            if log_jsonl:
                log_path = ws.local_path("api_logs.jsonl")
                log_path.write_text(log_jsonl)

        # --- PipelineWorkspace exit uploads results ---

        # Regen is admin-initiated and runs free to the user: preserve the
        # existing total_cost_usd (the API cost was still incurred by
        # Anthropic, but it shouldn't appear as user spend).
        proj_svc.update_project(
            storage, user_id, project_id,
            status="complete",
        )

        broker.publish(project_id, "pipeline_complete",
                       {"summary": meta.summary or {},
                        "regen_stages": stages})

    except (asyncio.CancelledError, CancelRequested):
        try:
            proj_svc.transition_status(
                storage, user_id, project_id,
                from_status={proj_svc.STATUS_RUNNING, proj_svc.STATUS_QUEUED},
                to_status=proj_svc.STATUS_CANCELLED,
                pipeline_state={"error": "Regen cancelled"},
                cancel_requested=False,
            )
        except proj_svc.StatusConflict:
            pass
        broker.publish(project_id, "pipeline_cancelled", {"error": "Regen cancelled"})

    except Exception as e:
        logger.exception("Regen pipeline crashed for project %s", project_id)
        try:
            proj_svc.transition_status(
                storage, user_id, project_id,
                from_status={proj_svc.STATUS_RUNNING, proj_svc.STATUS_QUEUED},
                to_status=proj_svc.STATUS_ERROR,
                pipeline_state={"error": str(e)},
                cancel_requested=False,
            )
        except proj_svc.StatusConflict:
            pass
        broker.publish(project_id, "pipeline_error", {"error": str(e)})


# Regen runs through the same Cloud Run Job worker as a full pipeline
# run; the API enqueues it via :mod:`backend.services.job_runner`.
