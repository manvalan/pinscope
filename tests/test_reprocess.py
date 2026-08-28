"""Reprocess a finished project: retry failed IC reviews or re-run all."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.services import projects as proj_svc
from backend.services.storage import LocalStorageBackend


def _client(tmp_path) -> TestClient:
    from backend.main import app

    app.state.storage = LocalStorageBackend(tmp_path)
    return TestClient(app)


def test_completed_review_refs_drops_failed_ics(storage):
    meta = proj_svc.create_project(storage, "local", "board")
    storage.write_json(
        f"users/local/projects/{meta.id}/report.json",
        {"review_errors": {"U19": "BadRequestError"}},
    )
    proj_svc.update_project(
        storage, "local", meta.id,
        completed_review_refs=["U1", "U19", "U3"],
        skipped_components=[
            {"identifier": "U19", "stage": "validation", "error": "400"},
        ],
    )
    kept = proj_svc.completed_review_refs_for_retry(storage, "local", meta.id)
    assert kept == ["U1", "U3"]


def test_reprocess_failed_enqueues_resume(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_enqueue(project_id, user_id, *, resume=False, free=False):
        captured.update(project_id=project_id, user_id=user_id, resume=resume, free=free)
        return "local/projects/x"

    monkeypatch.setattr("backend.services.job_runner.enqueue_pipeline", fake_enqueue)

    client = _client(tmp_path)
    meta = client.post("/api/projects", json={"name": "board"}).json()
    pid = meta["id"]
    storage = client.app.state.storage
    proj_svc.update_project(
        storage, "local", pid,
        status="complete",
        has_bom=True,
        has_netlist=True,
        completed_review_refs=["U1", "U2"],
        skipped_components=[
            {"identifier": "U2", "stage": "validation", "error": "400"},
        ],
    )

    resp = client.post(f"/api/pipeline/{pid}/reprocess", json={"mode": "failed"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["mode"] == "failed"
    assert captured["resume"] is True
    assert body["kept_review_refs"] == ["U1"]

    fresh = proj_svc.get_project(storage, "local", pid)
    assert fresh.status == "queued"
    assert fresh.completed_review_refs == ["U1"]


def test_reprocess_all_clears_kept_refs(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_enqueue(project_id, user_id, *, resume=False, free=False):
        captured["resume"] = resume
        return "local/projects/x"

    monkeypatch.setattr("backend.services.job_runner.enqueue_pipeline", fake_enqueue)

    client = _client(tmp_path)
    meta = client.post("/api/projects", json={"name": "board"}).json()
    pid = meta["id"]
    storage = client.app.state.storage
    proj_svc.update_project(
        storage, "local", pid,
        status="complete",
        has_bom=True,
        has_netlist=True,
        completed_review_refs=["U1"],
    )

    resp = client.post(f"/api/pipeline/{pid}/reprocess", json={"mode": "all"})
    assert resp.status_code == 202
    assert captured["resume"] is False
    fresh = proj_svc.get_project(storage, "local", pid)
    assert fresh.completed_review_refs == []


def test_reprocess_rejects_running(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.services.job_runner.enqueue_pipeline",
        lambda *a, **k: "x",
    )
    client = _client(tmp_path)
    meta = client.post("/api/projects", json={"name": "board"}).json()
    pid = meta["id"]
    storage = client.app.state.storage
    proj_svc.update_project(
        storage, "local", pid, status="running", has_bom=True, has_netlist=True,
    )
    resp = client.post(f"/api/pipeline/{pid}/reprocess", json={"mode": "failed"})
    assert resp.status_code == 409
