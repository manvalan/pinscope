"""Project storage via StorageBackend.

Each project lives at users/{user_id}/projects/{id}/ with:
  project.json              — metadata
  uploads/bom.csv           — uploaded BOM
  uploads/netlist.asc       — uploaded netlist
  uploads/datasheets/*.pdf  — uploaded datasheets
  extracted/                — IC extraction output
  patterns/                 — passive patterns
  models/                   — cached component specs
  design_graph.json         — graph output
  report.json               — validation report

Library (global, shared across users):
  library/extracted/{mpn}.json
  library/patterns/{mfr}_{type}.json
  library/datasheets/{mpn}.pdf
  library/models/{mpn}.json          — discrete/connector/crystal specs
  library/passives/{mpn}.json        — DigiKey-resolved passive specs
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from backend.pinscopex.utils import safe_mpn
from backend.services.storage import StaleGeneration, StorageBackend


class ProjectNotFound(Exception):
    """An operation targeted a project whose metadata is gone.

    Raised when ``project.json`` is missing — e.g. the project was deleted
    while a slow request (a large BOM upload) was still in flight. Callers /
    the global handler map this to a clean 404 instead of letting the raw
    storage NotFound bubble up as a 500 (which tears down the HTTP/2 stream
    mid-upload and surfaces in the browser as ERR_HTTP2_PROTOCOL_ERROR).
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        super().__init__(f"Project {project_id} not found")


# Statuses
STATUS_DRAFT = "draft"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
STATUS_PAUSED = "paused_insufficient_credits"

TERMINAL_STATUSES = frozenset({
    STATUS_COMPLETE, STATUS_ERROR, STATUS_CANCELLED, STATUS_PAUSED,
})


class StatusConflict(Exception):
    """Raised when a status transition's preconditions don't hold.

    Either the current status is not in ``from_status`` or another writer
    won the optimistic-concurrency race.
    """


class ProjectMeta(BaseModel):
    id: str
    name: str
    user_id: str = ""
    # draft | running | complete | error | cancelled
    # | paused_insufficient_credits | paused_by_user
    status: str = "draft"
    created: str = ""
    updated: str = ""
    has_bom: bool = False
    has_netlist: bool = False
    # "pads" | "edif" | None — None for legacy projects (pre-EDIF-support).
    # Legacy reads fall back to looking for netlist.asc on disk.
    netlist_format: str | None = None
    # When the EDIF file contains 2+ sub-designs, this is the list of
    # sub-design IDs (e.g. ["&0441"]) the user picked. None means "include
    # everything found in the file" — also the value when the netlist has a
    # single sub-design and no choice was offered.
    netlist_subdesigns: list[str] | None = None
    datasheet_count: int = 0
    summary: dict[str, int] | None = None
    component_mpns: dict[str, list[str]] | None = None  # {ic: [...], passive: [...]}
    bom_columns: dict[str, str] | None = None  # {reference: "...", mpn: "..."}
    # LCSC id → resolved manufacturer part number, populated by upload_bom when
    # the MPN column is detected as entirely LCSC ids (^C\d+$). The wizard UI
    # uses this to show "C12044 → STM32F103C8T6" alongside each row.
    lcsc_to_mpn: dict[str, str] | None = None
    # LCSC id → full purple-parts payload (mpn, manufacturer, package, description,
    # category, subcategory). Cached at upload time so the wizard's
    # /lcsc/resolve-passive endpoint can synthesize an auto-resolve call without
    # a second purple-parts round trip.
    lcsc_payloads: dict[str, dict] | None = None
    skipped_components: list[dict[str, str]] | None = None  # [{identifier, stage, error}]
    pipeline_state: dict[str, Any] | None = None
    total_cost_usd: float | None = None
    collaborators: list[str] = []  # Clerk user_ids with access to this project
    tier: str = "demo"  # user tier at project creation; "demo" default for pre-existing projects

    # Credit-system fields
    credits_spent: float = 0.0
    estimate: dict[str, Any] | None = None         # CostEstimate snapshot
    pause_checkpoint: dict[str, Any] | None = None  # PauseCheckpoint on paused runs
    pause_reason: str | None = None
    completed_review_refs: list[str] = []           # IC refs already reviewed (persists across pauses)

    # Pinscope app version that generated the project's report.
    # Stamped on the first /start transition and preserved thereafter.
    pinscope_version: str | None = None

    # Worker bookkeeping (set by the API on enqueue, read by /events SSE
    # and by the stale-running sweeper).
    execution_name: str | None = None
    queued_at: str | None = None
    # User-initiated cancel signal — the worker reads this in its cancel
    # gate (inside _charge_for_logs) and exits cleanly.
    cancel_requested: bool = False


def _project_prefix(user_id: str, project_id: str) -> str:
    return f"users/{user_id}/projects/{project_id}"


def _meta_key(user_id: str, project_id: str) -> str:
    return f"{_project_prefix(user_id, project_id)}/project.json"


def _read_meta(storage: StorageBackend, user_id: str, project_id: str) -> ProjectMeta:
    data = storage.read_json(_meta_key(user_id, project_id))
    return ProjectMeta.model_validate(data)


def _read_meta_with_generation(
    storage: StorageBackend, user_id: str, project_id: str
) -> tuple[ProjectMeta, int]:
    data, gen = storage.read_json_with_generation(_meta_key(user_id, project_id))
    return ProjectMeta.model_validate(data), gen


def _write_meta(storage: StorageBackend, meta: ProjectMeta) -> None:
    meta.updated = datetime.now(timezone.utc).isoformat()
    storage.write_json(
        _meta_key(meta.user_id, meta.id),
        meta.model_dump(),
    )


def transition_status(
    storage: StorageBackend,
    user_id: str,
    project_id: str,
    *,
    from_status: str | set[str] | frozenset[str],
    to_status: str,
    **fields: Any,
) -> ProjectMeta:
    """Move a project from ``from_status`` → ``to_status`` atomically.

    Reads the meta with its GCS generation, refuses the write if the
    current status isn't in ``from_status``, then issues a conditional
    write that fails if another writer raced in. Retries up to a few
    times on generation mismatch caused by unrelated field updates.

    Raises :class:`StatusConflict` when the current status doesn't match.
    """
    allowed: frozenset[str]
    if isinstance(from_status, str):
        allowed = frozenset({from_status})
    else:
        allowed = frozenset(from_status)

    last_exc: Exception | None = None
    for _ in range(5):
        meta, gen = _read_meta_with_generation(storage, user_id, project_id)
        if meta.status not in allowed:
            raise StatusConflict(
                f"project {project_id} is in status {meta.status!r}; "
                f"expected one of {sorted(allowed)} for transition to {to_status!r}"
            )
        meta.status = to_status
        for k, v in fields.items():
            setattr(meta, k, v)
        meta.updated = datetime.now(timezone.utc).isoformat()
        try:
            storage.write_json_if_match(
                _meta_key(user_id, project_id), meta.model_dump(), gen,
            )
            return meta
        except StaleGeneration as exc:
            last_exc = exc
            continue
    raise StatusConflict(
        f"project {project_id}: lost optimistic-concurrency race after retries"
    ) from last_exc


def request_cancel(
    storage: StorageBackend, user_id: str, project_id: str
) -> ProjectMeta:
    """Set ``cancel_requested = True`` so the worker's cancel gate trips.

    Does not touch ``status`` — the worker is responsible for moving the
    project to ``cancelled`` when it observes the flag.
    """
    return update_project(
        storage, user_id, project_id, cancel_requested=True,
    )


def mark_stale_running(
    storage: StorageBackend, user_id: str, project_id: str, error: str,
) -> ProjectMeta | None:
    """Flip a stale ``running`` project to ``error``. No-op otherwise.

    Returns the updated meta on success; ``None`` if the project's status
    was already terminal or the project no longer exists.
    """
    try:
        return transition_status(
            storage, user_id, project_id,
            from_status={STATUS_RUNNING, STATUS_QUEUED},
            to_status=STATUS_ERROR,
            pipeline_state={"error": error},
            cancel_requested=False,
        )
    except StatusConflict:
        return None


# --- CRUD ---


def create_project(storage: StorageBackend, user_id: str, name: str) -> ProjectMeta:
    project_id = uuid.uuid4().hex[:12]
    meta = ProjectMeta(
        id=project_id,
        name=name,
        user_id=user_id,
        created=datetime.now(timezone.utc).isoformat(),
    )
    _write_meta(storage, meta)
    return meta


def list_projects(storage: StorageBackend, user_id: str) -> list[ProjectMeta]:
    prefix = f"users/{user_id}/projects/"
    projects: list[ProjectMeta] = []
    for entry in storage.list_prefix(prefix):
        # entry is like users/{uid}/projects/{pid} (a directory)
        # or users/{uid}/projects/{pid}/project.json (a file)
        meta_key = f"{entry}/project.json" if not entry.endswith("/project.json") else entry
        if storage.exists(meta_key):
            data = storage.read_json(meta_key)
            projects.append(ProjectMeta.model_validate(data))
    return projects


def get_project(
    storage: StorageBackend, user_id: str, project_id: str
) -> ProjectMeta | None:
    key = _meta_key(user_id, project_id)
    if not storage.exists(key):
        return None
    return _read_meta(storage, user_id, project_id)


def update_project(
    storage: StorageBackend, user_id: str, project_id: str, **fields: Any
) -> ProjectMeta:
    if not storage.exists(_meta_key(user_id, project_id)):
        raise ProjectNotFound(project_id)
    meta = _read_meta(storage, user_id, project_id)
    for k, v in fields.items():
        setattr(meta, k, v)
    _write_meta(storage, meta)
    return meta


def delete_project(
    storage: StorageBackend, user_id: str, project_id: str
) -> bool:
    key = _meta_key(user_id, project_id)
    if not storage.exists(key):
        return False
    # Clean up shared references for all collaborators before deleting
    meta = _read_meta(storage, user_id, project_id)
    for collab_id in meta.collaborators:
        ref_key = _shared_ref_key(collab_id, project_id)
        if storage.exists(ref_key):
            storage.delete_key(ref_key)
    storage.delete_prefix(_project_prefix(user_id, project_id))
    return True


def clear_project_extractions(
    storage: StorageBackend, user_id: str, project_id: str
) -> None:
    """Delete per-project extraction JSONs and derived artifacts.

    Clears extracted/, patterns/, and models/ plus derived files (graph,
    power tree, BOM summary, derating, report, API logs) so the next
    pipeline run starts from fresh per-project data. The global library
    (library/*) is untouched — shared entries remain reusable.

    Meta fields tied to the prior run (summary, skipped list, review
    checkpoint, error state) are reset; historical spend fields
    (total_cost_usd, credits_spent) are preserved.
    """
    prefix = _project_prefix(user_id, project_id)
    for subdir in ("extracted", "patterns", "models"):
        storage.delete_prefix(f"{prefix}/{subdir}")
    for name in (
        "design_graph.json",
        "bom_summary.json",
        "derating.json",
        "report.json",
        "api_logs.jsonl",
        "graph_voltage_updates.json",
    ):
        key = f"{prefix}/{name}"
        if storage.exists(key):
            storage.delete_key(key)
    update_project(
        storage, user_id, project_id,
        summary=None,
        skipped_components=None,
        pipeline_state=None,
        pause_checkpoint=None,
        pause_reason=None,
        completed_review_refs=[],
    )


def reopen_project(
    storage: StorageBackend, user_id: str, project_id: str
) -> ProjectMeta:
    """Reset a finished/cancelled/errored project back to a draft-like state.

    Clears derived artifacts (graph, report, etc.) and the pause/review
    bookkeeping so the next pipeline run starts fresh, but preserves uploads,
    column mappings, and the extraction cache so the rerun reuses prior work
    cheaply.
    """
    prefix = _project_prefix(user_id, project_id)
    for name in (
        "design_graph.json",
        "bom_summary.json",
        "derating.json",
        "report.json",
        "api_logs.jsonl",
        "graph_voltage_updates.json",
    ):
        key = f"{prefix}/{name}"
        if storage.exists(key):
            storage.delete_key(key)
    return update_project(
        storage, user_id, project_id,
        status="draft",
        summary=None,
        skipped_components=None,
        pipeline_state=None,
        pause_checkpoint=None,
        pause_reason=None,
        completed_review_refs=[],
    )


def list_project_datasheets(
    storage: StorageBackend, user_id: str, project_id: str
) -> list[str]:
    """Return the safe-MPN stems of datasheet PDFs stored for a project."""
    ds_prefix = f"{_project_prefix(user_id, project_id)}/uploads/datasheets/"
    stems: list[str] = []
    for key in storage.list_prefix(ds_prefix):
        if key.endswith(".pdf"):
            stems.append(key.rsplit("/", 1)[-1][:-4])
    return stems


# --- Collaborator access resolution ---


def _shared_ref_key(user_id: str, project_id: str) -> str:
    return f"users/{user_id}/shared/{project_id}.json"


def resolve_project_access(
    storage: StorageBackend, caller_user_id: str, project_id: str
) -> tuple[str, ProjectMeta] | None:
    """Resolve project access for a user — checks ownership then collaborator refs.

    Returns (owner_user_id, ProjectMeta) or None if no access.
    """
    # 1. Direct ownership
    meta = get_project(storage, caller_user_id, project_id)
    if meta is not None:
        return (caller_user_id, meta)

    # 2. Shared reference
    ref_key = _shared_ref_key(caller_user_id, project_id)
    if not storage.exists(ref_key):
        return None
    ref = storage.read_json(ref_key)
    owner_id = ref.get("owner_user_id")
    if not owner_id:
        return None
    meta = get_project(storage, owner_id, project_id)
    if meta is None:
        return None
    # Verify caller is still in collaborators list
    if caller_user_id not in meta.collaborators:
        # Stale reference — clean up
        storage.delete_key(ref_key)
        return None
    return (owner_id, meta)


def find_project_any_user(
    storage: StorageBackend, project_id: str
) -> tuple[str, ProjectMeta] | None:
    """Scan all users to find a project by ID (for admin access).

    Returns (owner_user_id, ProjectMeta) or None.
    """
    seen_uids: set[str] = set()
    for entry in storage.list_prefix("users/"):
        parts = entry.split("/")
        if len(parts) >= 2:
            uid = parts[1]
            if uid in seen_uids:
                continue
            seen_uids.add(uid)
            meta = get_project(storage, uid, project_id)
            if meta is not None:
                return (uid, meta)
    return None


def add_collaborator(
    storage: StorageBackend, owner_user_id: str, project_id: str, collaborator_user_id: str
) -> ProjectMeta:
    """Add a collaborator to a project and write a shared reference."""
    meta = _read_meta(storage, owner_user_id, project_id)
    if collaborator_user_id not in meta.collaborators:
        meta.collaborators.append(collaborator_user_id)
        _write_meta(storage, meta)
    # Write reverse reference for the collaborator
    ref_key = _shared_ref_key(collaborator_user_id, project_id)
    storage.write_json(ref_key, {"owner_user_id": owner_user_id})
    return meta


def remove_collaborator(
    storage: StorageBackend, owner_user_id: str, project_id: str, collaborator_user_id: str
) -> ProjectMeta:
    """Remove a collaborator from a project and delete the shared reference."""
    meta = _read_meta(storage, owner_user_id, project_id)
    meta.collaborators = [c for c in meta.collaborators if c != collaborator_user_id]
    _write_meta(storage, meta)
    # Delete reverse reference
    ref_key = _shared_ref_key(collaborator_user_id, project_id)
    if storage.exists(ref_key):
        storage.delete_key(ref_key)
    return meta


def transfer_ownership(
    storage: StorageBackend,
    current_owner_user_id: str,
    project_id: str,
    new_owner_user_id: str,
) -> ProjectMeta:
    """Make an existing collaborator the new owner of a project.

    Swaps roles: ``new_owner_user_id`` becomes the owner, the previous owner
    is appended to ``collaborators``. All project files are physically moved
    from ``users/{old}/projects/{id}/`` to ``users/{new}/projects/{id}/`` so
    that the storage layout (which keys off the owner) stays consistent.
    Shared references are rewritten — the new owner's ref is deleted, the
    old owner gets one, and every remaining collaborator's ref is repointed
    at the new owner.

    Raises ``ValueError`` if the target is already the owner or is not a
    current collaborator.
    """
    meta = _read_meta(storage, current_owner_user_id, project_id)

    if new_owner_user_id == current_owner_user_id:
        raise ValueError("target user is already the owner")
    if new_owner_user_id not in meta.collaborators:
        raise ValueError("target user must currently be a collaborator")

    new_collaborators = [c for c in meta.collaborators if c != new_owner_user_id]
    if current_owner_user_id not in new_collaborators:
        new_collaborators.append(current_owner_user_id)

    meta.user_id = new_owner_user_id
    meta.collaborators = new_collaborators
    meta.updated = datetime.now(timezone.utc).isoformat()

    old_prefix = _project_prefix(current_owner_user_id, project_id)
    new_prefix = _project_prefix(new_owner_user_id, project_id)
    new_meta_key = _meta_key(new_owner_user_id, project_id)

    # Copy every file under the old prefix to the corresponding new key.
    # The old project.json is copied too — we overwrite it below with the
    # refreshed meta so the new location is authoritative even if a partial
    # failure leaves the old prefix in place.
    for old_key in storage.list_recursive(old_prefix):
        rel = old_key[len(old_prefix):].lstrip("/")
        storage.copy_object(old_key, f"{new_prefix}/{rel}")

    storage.write_json(new_meta_key, meta.model_dump())
    storage.delete_prefix(old_prefix)

    # Reverse references: new owner no longer needs one; old owner now does;
    # every other collaborator's existing ref must point at the new owner.
    new_owner_ref = _shared_ref_key(new_owner_user_id, project_id)
    if storage.exists(new_owner_ref):
        storage.delete_key(new_owner_ref)
    storage.write_json(
        _shared_ref_key(current_owner_user_id, project_id),
        {"owner_user_id": new_owner_user_id},
    )
    for collab_id in new_collaborators:
        if collab_id == current_owner_user_id:
            continue
        storage.write_json(
            _shared_ref_key(collab_id, project_id),
            {"owner_user_id": new_owner_user_id},
        )

    return meta


def list_shared_projects(storage: StorageBackend, user_id: str) -> list[ProjectMeta]:
    """List projects shared with a user (where they are a collaborator)."""
    prefix = f"users/{user_id}/shared/"
    shared: list[ProjectMeta] = []
    for entry in storage.list_prefix(prefix):
        if not entry.endswith(".json"):
            continue
        try:
            ref = storage.read_json(entry)
            owner_id = ref.get("owner_user_id")
            if not owner_id:
                continue
            # Extract project_id from the key: users/{uid}/shared/{project_id}.json
            filename = entry.rsplit("/", 1)[-1]
            project_id = filename.replace(".json", "")
            meta = get_project(storage, owner_id, project_id)
            if meta and user_id in meta.collaborators:
                shared.append(meta)
        except Exception:
            continue
    return shared


# --- File operations ---


def save_bom(
    storage: StorageBackend, user_id: str, project_id: str, data: bytes
) -> str:
    key = f"{_project_prefix(user_id, project_id)}/uploads/bom.csv"
    storage.write_bytes(key, data)
    update_project(storage, user_id, project_id, has_bom=True)
    return key


_NETLIST_EXT = {"pads": "asc", "edif": "edn"}


def _netlist_key(user_id: str, project_id: str, fmt: str) -> str:
    ext = _NETLIST_EXT.get(fmt, "asc")
    return f"{_project_prefix(user_id, project_id)}/uploads/netlist.{ext}"


def save_netlist(
    storage: StorageBackend,
    user_id: str,
    project_id: str,
    data: bytes,
    *,
    fmt: str = "pads",
) -> str:
    """Persist the uploaded netlist with the extension matching ``fmt``.

    Also clears any previously-saved netlist in the *other* format so we
    never have stale ``.asc`` and ``.edn`` files side-by-side (e.g. user
    re-uploads with a different format).
    """
    key = _netlist_key(user_id, project_id, fmt)
    storage.write_bytes(key, data)
    other_fmt = "edif" if fmt == "pads" else "pads"
    other_key = _netlist_key(user_id, project_id, other_fmt)
    if storage.exists(other_key):
        storage.delete_key(other_key)
    # Reset sub-design selection on every upload — the prior selection may
    # reference IDs that no longer exist in the new file. Frontend resets
    # the picker after upload too; this keeps backend in sync.
    update_project(
        storage, user_id, project_id,
        has_netlist=True, netlist_format=fmt, netlist_subdesigns=None,
    )
    return key


def save_datasheet(
    storage: StorageBackend, user_id: str, project_id: str, mpn: str, data: bytes
) -> str:
    """Save a datasheet PDF to the project uploads directory.

    Library writes happen during pattern extraction (one PDF per pattern series).
    """
    safe = safe_mpn(mpn)
    key = f"{_project_prefix(user_id, project_id)}/uploads/datasheets/{safe}.pdf"
    storage.write_bytes(key, data)
    # Count datasheets
    ds_prefix = f"{_project_prefix(user_id, project_id)}/uploads/datasheets/"
    count = sum(1 for k in storage.list_prefix(ds_prefix) if k.endswith(".pdf"))
    update_project(storage, user_id, project_id, datasheet_count=count)
    return key


def get_bom_key(
    storage: StorageBackend, user_id: str, project_id: str
) -> str | None:
    key = f"{_project_prefix(user_id, project_id)}/uploads/bom.csv"
    return key if storage.exists(key) else None


def get_netlist_key(
    storage: StorageBackend, user_id: str, project_id: str
) -> str | None:
    """Return the storage key of whichever netlist file exists (.asc or .edn)."""
    for fmt in ("pads", "edif"):
        key = _netlist_key(user_id, project_id, fmt)
        if storage.exists(key):
            return key
    return None


def get_datasheet_key(
    storage: StorageBackend, user_id: str, project_id: str, mpn: str
) -> str | None:
    safe = safe_mpn(mpn)
    key = f"{_project_prefix(user_id, project_id)}/uploads/datasheets/{safe}.pdf"
    return key if storage.exists(key) else None


def project_prefix(user_id: str, project_id: str) -> str:
    """Return the storage prefix for a project (for use by pipeline/routers)."""
    return _project_prefix(user_id, project_id)


# --- Library operations ---


def library_has_extraction(
    storage: StorageBackend, mpn: str, min_version: str | None = None,
) -> str | None:
    """Check if library has a complete extraction (with pintable) for this MPN.

    If *min_version* is set, also checks that the extraction's
    ``model_version`` meets the minimum threshold.
    Returns the key if found and valid, None otherwise.
    """
    safe = safe_mpn(mpn)
    key = f"library/extracted/{safe}.json"
    if not storage.exists(key):
        return None
    data = storage.read_json(key)
    if not data.get("pintable"):
        return None
    if min_version:
        from backend.services.admin_settings import version_is_stale

        component_version = data.get("model_version", "0.0.0")
        if version_is_stale(component_version, min_version):
            return None
    return key


def library_has_datasheet(
    storage: StorageBackend, mpn: str, patterns: list | None = None,
) -> str | None:
    """Check if library has a datasheet PDF for this MPN.

    Checks content-addressed refs first, then falls back to legacy flat
    files (for pre-migration data), then pattern-based lookup.

    Returns the storage key if found, None otherwise.
    """
    from backend.services.datasheet_store import resolve_datasheet

    # 1. Content-addressed ref lookup
    resolved = resolve_datasheet(storage, mpn)
    if resolved:
        return resolved
    # 2. Legacy flat file fallback (remove after migration confirmed)
    safe = safe_mpn(mpn)
    key = f"library/datasheets/{safe}.pdf"
    if storage.exists(key):
        return key
    # 3. Pattern-based fallback for passives
    if patterns:
        from backend.pinscopex.resolve_passives import resolve_mpn

        match = resolve_mpn(mpn, patterns)
        if match is not None:
            pat = match[0]
            ds_key = pat.datasheet_key
            if ds_key and storage.exists(ds_key):
                return ds_key
    return None


def library_has_model(storage: StorageBackend, mpn: str) -> str | None:
    """Check if library has a ComponentModel (specs) for this MPN.

    Returns the key if found, None otherwise.
    """
    safe = safe_mpn(mpn)
    key = f"library/models/{safe}.json"
    return key if storage.exists(key) else None


def library_has_passive_model(storage: StorageBackend, mpn: str) -> str | None:
    """Check if library has a DigiKey-resolved passive model for this MPN.

    Checks library/passives/ first, then falls back to library/models/
    for pre-migration data. Returns the key if found, None otherwise.
    """
    safe = safe_mpn(mpn)
    key = f"library/passives/{safe}.json"
    if storage.exists(key):
        return key
    # Fallback: pre-migration passive specs may still be in library/models/
    legacy_key = f"library/models/{safe}.json"
    return legacy_key if storage.exists(legacy_key) else None


def save_to_library(
    storage: StorageBackend, src_key: str, category: str, filename: str
) -> str:
    """Copy a file to the shared library."""
    dst_key = f"library/{category}/{filename}"
    storage.copy_object(src_key, dst_key)
    return dst_key


def list_library_patterns(storage: StorageBackend) -> list[str]:
    """List all pattern keys in the library."""
    prefix = "library/patterns/"
    return [k for k in storage.list_prefix(prefix) if k.endswith(".json")]


def load_library_patterns(storage: StorageBackend):
    """Load and parse all passive patterns from the library.

    For local backend, delegates to pinscopex. For GCS, downloads to temp first.
    This function is only used by the library/check endpoint — during pipeline
    execution, patterns are loaded from the workspace temp directory.
    """
    from backend.pinscopex.resolve_passives import load_patterns

    from backend.services.storage import LocalStorageBackend

    if isinstance(storage, LocalStorageBackend):
        d = storage._path("library/patterns")
        if not d.is_dir():
            return []
        return load_patterns(str(d))

    # GCS: download patterns to a temp directory
    import tempfile

    pattern_keys = list_library_patterns(storage)
    if not pattern_keys:
        return []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "patterns"
        tmp_path.mkdir()
        for key in pattern_keys:
            filename = key.rsplit("/", 1)[-1]
            storage.download_to_local(key, tmp_path / filename)
        return load_patterns(str(tmp_path))


# Re-export for convenience
from pathlib import Path  # noqa: E402
