"""Wave H — finding review state, ECO, release signature.

Favor: false_positive/accepted/wontfix with a reason persist; accepted
rows land in eco.json; signature hashes the findings payload.
Against: empty reason; unknown state; false_positive is not an ECO row;
unsigned report has no release block.
"""

from __future__ import annotations

import pytest

from backend.pinscopex.models import Finding
from backend.pinscopex.review_workflow import (
    ReviewError,
    apply_review_state,
    build_eco,
    eco_csv,
    sign_report,
)


def _finding(**kwargs):
    defaults = dict(
        finding_id="U1-001",
        designator="U1",
        mpn="PART",
        aspect="decoupling",
        finding="missing cap",
        why="no 100nF on VDD",
        status="ERROR",
        recommendation="add 100nF",
        rule_id="PS-DEC-001",
    )
    defaults.update(kwargs)
    return Finding(**defaults)


def test_false_positive_requires_reason():
    with pytest.raises(ReviewError):
        apply_review_state({}, "U1-001", state="false_positive", reason="  ", user_id="local")


def test_unknown_state_is_rejected():
    with pytest.raises(ReviewError):
        apply_review_state({}, "U1-001", state="fixed", reason="ok", user_id="local")


def test_accepted_with_reason_is_stored():
    states = apply_review_state(
        {}, "U1-001", state="accepted", reason="will spin ECO-12", user_id="local",
        user_name="Michele",
    )
    assert states["U1-001"]["state"] == "accepted"
    assert states["U1-001"]["reason"] == "will spin ECO-12"
    assert states["U1-001"]["user_id"] == "local"


def test_eco_includes_accepted_not_false_positive():
    findings = [
        _finding(finding_id="U1-001"),
        _finding(finding_id="U2-001", designator="U2", finding="noise"),
    ]
    states = apply_review_state({}, "U1-001", state="accepted", reason="add cap", user_id="a")
    states = apply_review_state(states, "U2-001", state="false_positive", reason="ok in app", user_id="a")
    eco = build_eco(findings, states)
    assert [row["finding_id"] for row in eco] == ["U1-001"]
    assert eco[0]["rule_id"] == "PS-DEC-001"
    assert eco[0]["ref"] == "U1"
    assert "100nF" in eco[0]["after"]
    csv = eco_csv(eco)
    assert "U1-001" in csv
    assert "U2-001" not in csv


def test_open_and_wontfix_are_not_eco_rows():
    findings = [_finding()]
    states = apply_review_state({}, "U1-001", state="wontfix", reason="wont ship", user_id="a")
    assert build_eco(findings, states) == []
    assert build_eco(findings, {}) == []


def test_signature_changes_when_findings_change():
    a = sign_report({"findings": [{"finding_id": "U1-001"}]}, user_id="local")
    b = sign_report({"findings": [{"finding_id": "U1-002"}]}, user_id="local")
    assert a["user_id"] == "local"
    assert a["sha256"] != b["sha256"]
    assert a["timestamp"]


def _client(tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.services.storage import LocalStorageBackend

    app.state.storage = LocalStorageBackend(tmp_path)
    return TestClient(app)


def _seed_report(client, findings):
    meta = client.post("/api/projects", json={"name": "board"}).json()
    pid = meta["id"]
    storage = client.app.state.storage
    prefix = f"users/local/projects/{pid}"
    storage.write_json(f"{prefix}/report.json", {
        "project": "board",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "findings": [f.model_dump() for f in findings],
        "summary": {"total": len(findings), "ERROR": 1, "WARNING": 0, "INFO": 0},
    })
    return pid


def test_api_review_without_reason_is_400(tmp_path):
    client = _client(tmp_path)
    pid = _seed_report(client, [_finding()])
    res = client.put(f"/api/report/{pid}/findings/U1-001/review", json={
        "state": "accepted", "reason": "",
    })
    assert res.status_code == 400


def test_api_review_and_eco(tmp_path):
    client = _client(tmp_path)
    pid = _seed_report(client, [_finding()])
    res = client.put(f"/api/report/{pid}/findings/U1-001/review", json={
        "state": "accepted", "reason": "add 100nF near U1.3",
    })
    assert res.status_code == 200
    report = client.get(f"/api/report/{pid}").json()
    assert report["review_states"]["U1-001"]["state"] == "accepted"
    eco = client.get(f"/api/report/{pid}/eco.json").json()
    assert eco["items"][0]["finding_id"] == "U1-001"
    csv = client.get(f"/api/report/{pid}/eco.csv")
    assert csv.status_code == 200
    assert "U1-001" in csv.text


def test_api_unknown_finding_is_404(tmp_path):
    client = _client(tmp_path)
    pid = _seed_report(client, [_finding()])
    res = client.put(f"/api/report/{pid}/findings/NOPE/review", json={
        "state": "accepted", "reason": "x",
    })
    assert res.status_code == 404


def test_api_sign_release(tmp_path):
    client = _client(tmp_path)
    pid = _seed_report(client, [_finding()])
    res = client.post(f"/api/report/{pid}/sign")
    assert res.status_code == 200
    body = res.json()
    assert len(body["sha256"]) == 64
    report = client.get(f"/api/report/{pid}").json()
    assert report["release"]["sha256"] == body["sha256"]
    assert report["release"]["user_id"] == "local"
