"""Tests for the LCSC → MPN resolver wired into the BOM-parse stage."""

from __future__ import annotations

import pytest


def test_is_lcsc_code():
    from backend.services.purple_parts import is_lcsc_code

    assert is_lcsc_code("C12345") is True
    assert is_lcsc_code("c123") is True
    assert is_lcsc_code("C0") is True
    assert is_lcsc_code("TPS62840DLCR") is False
    assert is_lcsc_code("C") is False
    assert is_lcsc_code("C123abc") is False
    assert is_lcsc_code("") is False
    assert is_lcsc_code(None) is False
    assert is_lcsc_code("  C12345  ") is True


@pytest.mark.asyncio
async def test_resolve_lcsc_codes_no_op_when_disabled(monkeypatch):
    """When purple-parts isn't configured, the helper must not touch the BOM."""
    from backend.config import settings
    from backend.services.pipeline import _resolve_lcsc_codes

    monkeypatch.setattr(settings, "purple_parts_url", "")
    monkeypatch.setattr(settings, "purple_parts_api_key", "")

    bom = {
        "U1": {"value": "", "footprint": "", "mpn": None, "lcsc": "C12345"},
        "C1": {"value": "10uF", "footprint": "0603", "mpn": "C25804", "lcsc": None},
    }
    snapshot = {k: dict(v) for k, v in bom.items()}

    await _resolve_lcsc_codes(ctx=None, bom=bom)

    assert bom == snapshot


@pytest.mark.asyncio
async def test_resolve_lcsc_codes_fills_mpn_from_lcsc_column(monkeypatch):
    """LCSC code in the dedicated `lcsc` column populates the empty `mpn`."""
    from backend.config import settings
    from backend.services import pipeline, purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    async def fake_lookup(codes):
        assert sorted(codes) == ["C12345"]
        return {"C12345": {"lcsc": "C12345", "mpn": "TPS62840DLCR", "manufacturer": "TI"}}

    monkeypatch.setattr(purple_parts, "lookup_lcsc_batch", fake_lookup)

    class _Ctx:
        project_id = "p1"
        lcsc_data: dict = {}

    monkeypatch.setattr(pipeline.broker, "publish", lambda *a, **kw: None)

    bom = {
        "U1": {"value": "", "footprint": "LQFP-48", "mpn": None, "lcsc": "C12345"},
    }
    await pipeline._resolve_lcsc_codes(_Ctx(), bom)

    assert bom["U1"]["mpn"] == "TPS62840DLCR"


@pytest.mark.asyncio
async def test_resolve_lcsc_codes_pipeline_backstop_is_lcsc_column_only(monkeypatch):
    """Pipeline-stage resolver is a backstop for the unambiguous case only:
    LCSC column populated AND MPN slot empty. An LCSC-shaped value sitting
    in the MPN slot is NOT resolved here — column-level upload-time
    resolution is the primary path."""
    from backend.config import settings
    from backend.services import pipeline, purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    called = False

    async def fake_lookup(codes):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(purple_parts, "lookup_lcsc_batch", fake_lookup)
    monkeypatch.setattr(pipeline.broker, "publish", lambda *a, **kw: None)

    class _Ctx:
        project_id = "p1"
        lcsc_data: dict = {}

    bom = {
        # MPN field has an LCSC-shaped value — pipeline backstop must NOT touch this.
        "U2": {"value": "", "footprint": "", "mpn": "C2040", "lcsc": None},
        # A real MPN starting with C must NOT be touched.
        "C1": {"value": "10uF", "footprint": "0402", "mpn": "CL05B104KO5NNNC", "lcsc": None},
    }
    await pipeline._resolve_lcsc_codes(_Ctx(), bom)

    assert called is False
    assert bom["U2"]["mpn"] == "C2040"
    assert bom["C1"]["mpn"] == "CL05B104KO5NNNC"


@pytest.mark.asyncio
async def test_resolve_lcsc_codes_leaves_unresolved_alone(monkeypatch):
    """A miss in purple-parts must not blank out the existing MPN/LCSC."""
    from backend.config import settings
    from backend.services import pipeline, purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    async def fake_lookup(codes):
        return {"C99999999": None}

    monkeypatch.setattr(purple_parts, "lookup_lcsc_batch", fake_lookup)
    monkeypatch.setattr(pipeline.broker, "publish", lambda *a, **kw: None)

    class _Ctx:
        project_id = "p1"
        lcsc_data: dict = {}

    bom = {
        "U3": {"value": "", "footprint": "", "mpn": None, "lcsc": "C99999999"},
    }
    await pipeline._resolve_lcsc_codes(_Ctx(), bom)

    assert bom["U3"]["mpn"] is None  # unchanged
    assert bom["U3"]["lcsc"] == "C99999999"


@pytest.mark.asyncio
async def test_resolve_lcsc_codes_caches_payload_for_passive_stage(monkeypatch):
    """The resolver must stash the full LCSC payload on ctx.lcsc_data so the
    downstream passive extraction can use the description/category without a
    second purple-parts call."""
    from backend.config import settings
    from backend.services import pipeline, purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    payload = {
        "lcsc": "C15850",
        "mpn": "CL21A106KAYNNNE",
        "manufacturer": "Samsung",
        "package": "0805",
        "description": "10uF 25V X5R ±10% 0805 MLCC",
        "category": "Capacitors",
        "subcategory": "MLCC - SMD/SMT",
    }

    async def fake_lookup(codes):
        return {"C15850": payload}

    monkeypatch.setattr(purple_parts, "lookup_lcsc_batch", fake_lookup)
    monkeypatch.setattr(pipeline.broker, "publish", lambda *a, **kw: None)

    class _Ctx:
        project_id = "p1"
        lcsc_data: dict = {}

    ctx = _Ctx()
    bom = {
        "C1": {"value": "10uF", "footprint": "0805", "mpn": None, "lcsc": "C15850"},
        "C2": {"value": "10uF", "footprint": "0805", "mpn": None, "lcsc": "C15850"},
    }
    await pipeline._resolve_lcsc_codes(ctx, bom)

    assert ctx.lcsc_data == {"CL21A106KAYNNNE": payload}
    assert bom["C1"]["mpn"] == "CL21A106KAYNNNE"
    assert bom["C2"]["mpn"] == "CL21A106KAYNNNE"


def test_detect_lcsc_column_all_c_codes():
    from backend.services.purple_parts import detect_lcsc_column

    csv = b"Reference,Manufacturer Part Number,Value\nU1,C12044,\nU2,C2040,\nU3,C25804,\n"
    assert detect_lcsc_column(csv, "Manufacturer Part Number") is True


def test_detect_lcsc_column_mixed_returns_false():
    from backend.services.purple_parts import detect_lcsc_column

    csv = (b"Reference,Manufacturer Part Number\n"
           b"U1,C12044\n"
           b"U2,TPS62840DLCR\n")
    assert detect_lcsc_column(csv, "Manufacturer Part Number") is False


def test_detect_lcsc_column_empty_returns_false():
    from backend.services.purple_parts import detect_lcsc_column

    csv = b"Reference,Manufacturer Part Number\nU1,\nU2,\n"
    assert detect_lcsc_column(csv, "Manufacturer Part Number") is False


def test_detect_lcsc_column_missing_column():
    from backend.services.purple_parts import detect_lcsc_column

    csv = b"Reference,Value\nU1,10uF\n"
    assert detect_lcsc_column(csv, "Manufacturer Part Number") is False


@pytest.mark.asyncio
async def test_resolve_lcsc_column_bytes_rewrites_csv(monkeypatch):
    from backend.config import settings
    from backend.services import purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    async def fake_lookup(codes):
        return {
            "C12044": {"lcsc": "C12044", "mpn": "STM32F103C8T6"},
            "C2040":  {"lcsc": "C2040",  "mpn": "RP2040"},
            "C99999999": None,  # unresolved
        }

    monkeypatch.setattr(purple_parts, "lookup_lcsc_batch", fake_lookup)

    csv_in = (
        b"Reference,Value,Footprint,Manufacturer Part Number\n"
        b"U1,STM32,LQFP-48,C12044\n"
        b"U2,RP2040,QFN-56,C2040\n"
        b"U3,Mystery,SOT-23,C99999999\n"
    )
    out_bytes, updated, lcsc_map, lcsc_payloads = await purple_parts.resolve_lcsc_column_bytes(
        csv_in, mpn_col="Manufacturer Part Number",
    )
    assert updated == 2
    assert lcsc_map == {"C12044": "STM32F103C8T6", "C2040": "RP2040"}
    # Full payloads keyed by LCSC id (verbatim from purple-parts) so the wizard
    # can short-cut DigiKey when synthesising auto_resolve_specs input.
    assert set(lcsc_payloads.keys()) == {"C12044", "C2040"}
    assert lcsc_payloads["C12044"]["mpn"] == "STM32F103C8T6"
    out_text = out_bytes.decode()
    # Resolved rows updated
    assert "STM32F103C8T6" in out_text
    assert "RP2040" in out_text
    # Unresolved row preserved as-is
    assert "C99999999" in out_text
    # Column order and other columns preserved
    assert out_text.splitlines()[0] == "Reference,Value,Footprint,Manufacturer Part Number"
    assert "LQFP-48" in out_text


@pytest.mark.asyncio
async def test_resolve_lcsc_column_bytes_no_op_when_disabled(monkeypatch):
    from backend.config import settings
    from backend.services import purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "")
    monkeypatch.setattr(settings, "purple_parts_api_key", "")

    csv_in = b"Reference,Manufacturer Part Number\nU1,C12044\n"
    out_bytes, updated, lcsc_map, lcsc_payloads = await purple_parts.resolve_lcsc_column_bytes(
        csv_in, mpn_col="Manufacturer Part Number",
    )
    assert updated == 0
    assert lcsc_map == {}
    assert lcsc_payloads == {}
    assert out_bytes == csv_in


@pytest.mark.asyncio
async def test_resolve_lcsc_codes_skips_rows_with_real_mpn(monkeypatch):
    """Rows that already have a real MPN should not be re-resolved."""
    from backend.config import settings
    from backend.services import pipeline, purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    call_count = 0

    async def fake_lookup(codes):
        nonlocal call_count
        call_count += 1
        return {c: None for c in codes}

    monkeypatch.setattr(purple_parts, "lookup_lcsc_batch", fake_lookup)
    monkeypatch.setattr(pipeline.broker, "publish", lambda *a, **kw: None)

    class _Ctx:
        project_id = "p1"
        lcsc_data: dict = {}

    bom = {
        "C1": {"value": "10uF", "footprint": "0603", "mpn": "GRM188R71C104KA01D", "lcsc": "C14663"},
    }
    await pipeline._resolve_lcsc_codes(_Ctx(), bom)

    assert call_count == 0  # nothing to resolve
    assert bom["C1"]["mpn"] == "GRM188R71C104KA01D"


# ---------------------------------------------------------------------------
# Upload-time classification + lcsc_payloads caching + /lcsc/resolve-passive
# ---------------------------------------------------------------------------


def _make_test_client(tmp_path):
    """Build a FastAPI TestClient backed by a fresh LocalStorageBackend.

    Mirrors the production app's middleware stack but defaults the user_id
    to ``"local"`` (auth disabled) so we can hit endpoints without Clerk.
    """
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services.storage import LocalStorageBackend

    app.state.storage = LocalStorageBackend(tmp_path)
    return TestClient(app)


def test_upload_bom_classifies_components(tmp_path, monkeypatch):
    """Upload-time component classification populates ``component_mpns``
    for a mixed-type BOM, even without LCSC resolution running."""
    from backend.config import settings

    # Disable purple-parts so we exercise the no-LCSC path.
    monkeypatch.setattr(settings, "purple_parts_url", "")
    monkeypatch.setattr(settings, "purple_parts_api_key", "")

    client = _make_test_client(tmp_path)

    # Create project and upload a mixed BOM.
    resp = client.post("/api/projects", json={"name": "mixed"})
    assert resp.status_code == 200
    project_id = resp.json()["id"]

    csv = (
        b"Reference,Value,Footprint,Manufacturer Part Number\n"
        b"U1,STM32,LQFP-48,STM32F103C8T6\n"
        b"C1,10uF,0603,CL10A106KQ8NNNC\n"
        b"R1,10k,0603,RC0603FR-0710KL\n"
        b"D1,Diode,SOD-123,1N4148WS\n"
    )
    files = {"file": ("bom.csv", csv, "text/csv")}
    resp = client.post(
        f"/api/projects/{project_id}/upload/bom",
        files=files,
    )
    assert resp.status_code == 200, resp.text
    # The classification should land on ProjectMeta.component_mpns.
    resp = client.get(f"/api/projects/{project_id}")
    meta = resp.json()
    assert meta["component_mpns"] is not None
    assert meta["component_mpns"]["ic"] == ["STM32F103C8T6"]
    assert sorted(meta["component_mpns"]["passive"]) == sorted(
        ["CL10A106KQ8NNNC", "RC0603FR-0710KL"]
    )
    assert meta["component_mpns"]["simple"] == ["1N4148WS"]


def test_upload_bom_populates_lcsc_payloads(tmp_path, monkeypatch):
    """When the MPN column is detected as all-LCSC ids, the full purple-parts
    payloads must be cached on ProjectMeta.lcsc_payloads."""
    from backend.config import settings
    from backend.services import purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    async def fake_lookup(codes):
        return {
            "C15850": {
                "lcsc": "C15850",
                "mpn": "CL21A106KAYNNNE",
                "manufacturer": "Samsung",
                "package": "0805",
                "description": "10uF 25V X5R 0805 MLCC",
                "category": "Capacitors",
                "subcategory": "MLCC - SMD/SMT",
            },
            "C14663": {
                "lcsc": "C14663",
                "mpn": "GRM188R71C104KA01D",
                "manufacturer": "Murata",
                "package": "0603",
                "description": "0.1uF 16V X7R 0603 MLCC",
                "category": "Capacitors",
                "subcategory": "MLCC - SMD/SMT",
            },
        }

    monkeypatch.setattr(purple_parts, "lookup_lcsc_batch", fake_lookup)

    client = _make_test_client(tmp_path)
    resp = client.post("/api/projects", json={"name": "lcsc"})
    project_id = resp.json()["id"]

    csv = (
        b"Reference,Value,Footprint,Manufacturer Part Number\n"
        b"C1,10uF,0805,C15850\n"
        b"C2,0.1uF,0603,C14663\n"
    )
    resp = client.post(
        f"/api/projects/{project_id}/upload/bom",
        files={"file": ("bom.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lcsc_resolved"] == 2
    assert body["lcsc_to_mpn"] == {
        "C15850": "CL21A106KAYNNNE", "C14663": "GRM188R71C104KA01D",
    }

    meta = client.get(f"/api/projects/{project_id}").json()
    assert meta["lcsc_payloads"] is not None
    assert set(meta["lcsc_payloads"].keys()) == {"C15850", "C14663"}
    p = meta["lcsc_payloads"]["C15850"]
    assert p["mpn"] == "CL21A106KAYNNNE"
    assert p["description"].startswith("10uF")
    # Component classification ran on the resolved BOM, so the MPNs are the
    # real manufacturer parts (not the LCSC codes).
    assert sorted(meta["component_mpns"]["passive"]) == sorted(
        ["CL21A106KAYNNNE", "GRM188R71C104KA01D"]
    )


def test_lcsc_resolve_passive_404_when_lcsc_id_missing(tmp_path, monkeypatch):
    """404 when the requested lcsc_id isn't in the project's cached payloads."""
    from backend.config import settings

    monkeypatch.setattr(settings, "purple_parts_url", "")
    monkeypatch.setattr(settings, "purple_parts_api_key", "")

    client = _make_test_client(tmp_path)
    resp = client.post("/api/projects", json={"name": "p"})
    project_id = resp.json()["id"]

    resp = client.post(
        f"/api/projects/{project_id}/lcsc/resolve-passive",
        json={"lcsc_id": "C99999999"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_lcsc_resolve_passive_success_and_cached(tmp_path, monkeypatch):
    """First call resolves via auto_resolve_specs (mocked) and writes the
    project model + library copy. Second call short-circuits with cached=True
    and does not invoke auto_resolve_specs again."""
    from backend.config import settings
    from backend.pinscopex.models import CapacitorSpecs, ComponentModel
    from backend.services import purple_parts
    from backend.services import extraction as extraction_svc

    # Billing modules are absent in the open-source repo; the endpoint under
    # test only charges when billing is enabled, so skip there.
    credits_svc = pytest.importorskip("backend.services.credits")

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    payload = {
        "lcsc": "C15850",
        "mpn": "CL21A106KAYNNNE",
        "manufacturer": "Samsung",
        "package": "0805",
        "description": "10uF 25V X5R 0805 MLCC",
        "category": "Capacitors",
        "subcategory": "MLCC - SMD/SMT",
    }

    async def fake_lookup(codes):
        return {"C15850": payload}

    monkeypatch.setattr(purple_parts, "lookup_lcsc_batch", fake_lookup)

    call_count = {"n": 0}

    async def fake_auto_resolve_specs(**kwargs):
        call_count["n"] += 1
        # Drop a synthetic api log entry so the credit-charge path runs.
        api_logger = kwargs.get("api_logger")
        if api_logger is not None:
            api_logger.log(
                stage="auto_resolve", identifier=kwargs["mpn"],
                model="claude-haiku-4-5-20251001",
                input_tokens=500, output_tokens=200,
                cache_creation_input_tokens=0, cache_read_input_tokens=0,
                duration_ms=100, stop_reason="end_turn", turns=1,
            )
        return ComponentModel(
            mpn=kwargs["mpn"],
            specs=CapacitorSpecs(
                value_farads=10e-6,
                value_formatted="10uF",
                voltage_rating_v="25V",
                tolerance="±10%",
                dielectric="X5R",
                package="0805",
            ),
        )

    monkeypatch.setattr(extraction_svc, "auto_resolve_specs", fake_auto_resolve_specs)

    # Need credits to charge against — grant enough.
    from backend.services.storage import LocalStorageBackend
    storage = LocalStorageBackend(tmp_path)
    credits_svc.grant(storage, "local", 100.0, "trial_grant")

    from backend.main import app
    from fastapi.testclient import TestClient

    app.state.storage = storage
    client = TestClient(app)

    resp = client.post("/api/projects", json={"name": "lcsc"})
    project_id = resp.json()["id"]

    csv = b"Reference,Value,Footprint,Manufacturer Part Number\nC1,10uF,0805,C15850\n"
    resp = client.post(
        f"/api/projects/{project_id}/upload/bom",
        files={"file": ("bom.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text

    # First call: resolves via mocked auto_resolve_specs.
    resp = client.post(
        f"/api/projects/{project_id}/lcsc/resolve-passive",
        json={"lcsc_id": "C15850"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mpn"] == "CL21A106KAYNNNE"
    assert body["lcsc_id"] == "C15850"
    assert body["cached"] is False
    assert body["model"]["mpn"] == "CL21A106KAYNNNE"
    assert call_count["n"] == 1

    # Library copy should exist for cross-project reuse.
    from backend.pinscopex.utils import safe_mpn
    safe = safe_mpn("CL21A106KAYNNNE")
    assert storage.exists(f"library/passives/{safe}.json")

    # Second call: short-circuits via the project model file.
    resp = client.post(
        f"/api/projects/{project_id}/lcsc/resolve-passive",
        json={"lcsc_id": "C15850"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cached"] is True
    assert body["model"]["mpn"] == "CL21A106KAYNNNE"
    assert call_count["n"] == 1  # not invoked again


# ---------------------------------------------------------------------------
# Reverse MPN → LCSC lookup (by-mpn) + upload-time real-MPN passive enrich
# ---------------------------------------------------------------------------


def test_pick_exact_matches_case_and_space_insensitive():
    from backend.services.purple_parts import _pick_exact

    cands = [
        {"mpn": "OTHER-PART", "lcsc": "C1"},
        {"mpn": "tps62840 dlcr", "lcsc": "C2040"},
    ]
    hit = _pick_exact("TPS62840DLCR", cands)
    assert hit is not None
    assert hit["lcsc"] == "C2040"


def test_pick_exact_rejects_prefix_only():
    from backend.services.purple_parts import _pick_exact

    # Only a longer family part is returned — not an exact match → reject so it
    # can't pollute the shared library.
    cands = [{"mpn": "RC0603FR-0710KLZZ", "lcsc": "C999"}]
    assert _pick_exact("RC0603FR-0710KL", cands) is None
    assert _pick_exact("X", []) is None


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient used by lookup_mpn_batch — serves the
    POST /v1/parts/by-mpn/batch endpoint from a {mpn: [candidates]} routes map."""

    def __init__(self, routes):
        self.routes = routes
        self.posts: list[dict] = []  # captured request bodies

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        body = json or {}
        self.posts.append(body)
        mpns = body.get("mpns", [])
        return _FakeResp({"results": {m: self.routes.get(m, []) for m in mpns}})


@pytest.mark.asyncio
async def test_lookup_mpn_batch_disabled_is_noop(monkeypatch):
    from backend.config import settings
    from backend.services import purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "")
    monkeypatch.setattr(settings, "purple_parts_api_key", "")

    out = await purple_parts.lookup_mpn_batch(["TPS62840DLCR"])
    assert out == {"TPS62840DLCR": None}


@pytest.mark.asyncio
async def test_lookup_mpn_batch_exact_dedup_and_miss(monkeypatch):
    from backend.config import settings
    from backend.services import purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    async def fake_token():
        return "tok"

    monkeypatch.setattr(purple_parts, "_get_identity_token", fake_token)

    routes = {
        "TPS62840DLCR": [{"lcsc": "C2040", "mpn": "TPS62840DLCR", "description": "buck"}],
        "RC0603FR-0710KL": [
            {"lcsc": "C999", "mpn": "RC0603FR-0710KLZZ", "description": "prefix only"}
        ],
        "MISSING1": [],
    }
    fake = _FakeAsyncClient(routes)
    monkeypatch.setattr(purple_parts.httpx, "AsyncClient", lambda *a, **kw: fake)

    out = await purple_parts.lookup_mpn_batch(
        ["TPS62840DLCR", "TPS62840DLCR", "RC0603FR-0710KL", "MISSING1"]
    )

    assert out["TPS62840DLCR"]["lcsc"] == "C2040"      # exact hit kept
    assert out["RC0603FR-0710KL"] is None               # prefix-only rejected
    assert out["MISSING1"] is None                      # real miss
    # Deduped into a single batch POST; TPS appears once in the request body.
    assert len(fake.posts) == 1
    assert fake.posts[0]["mpns"].count("TPS62840DLCR") == 1


@pytest.mark.asyncio
async def test_lookup_mpn_batch_no_token_returns_all_none(monkeypatch):
    from backend.config import settings
    from backend.services import purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    async def no_token():
        return None

    monkeypatch.setattr(purple_parts, "_get_identity_token", no_token)

    out = await purple_parts.lookup_mpn_batch(["TPS62840DLCR", "RP2040"])
    assert out == {"TPS62840DLCR": None, "RP2040": None}


@pytest.mark.asyncio
async def test_lookup_mpn_batch_chunks_large_input(monkeypatch):
    """More than _BATCH_SIZE unique MPNs are split across multiple POSTs."""
    from backend.config import settings
    from backend.services import purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")
    monkeypatch.setattr(purple_parts, "_BATCH_SIZE", 2)

    async def fake_token():
        return "tok"

    monkeypatch.setattr(purple_parts, "_get_identity_token", fake_token)

    routes = {m: [{"lcsc": f"C{i}", "mpn": m}] for i, m in enumerate(["A", "B", "C"])}
    fake = _FakeAsyncClient(routes)
    monkeypatch.setattr(purple_parts.httpx, "AsyncClient", lambda *a, **kw: fake)

    out = await purple_parts.lookup_mpn_batch(["A", "B", "C"])

    assert {k: v["lcsc"] for k, v in out.items()} == {"A": "C0", "B": "C1", "C": "C2"}
    assert len(fake.posts) == 2                      # 3 MPNs / chunk size 2
    assert [len(p["mpns"]) for p in fake.posts] == [2, 1]


def test_upload_bom_enriches_real_mpn_passives_via_by_mpn(tmp_path, monkeypatch):
    """For a genuine MPN column, upload reverse-looks-up passives in the LCSC
    catalogue and caches lcsc_to_mpn + lcsc_payloads so the wizard resolves them
    through the same machinery as the LCSC-column flow. ICs/simple are excluded,
    catalogue misses fall through to the pipeline, and the CSV is not rewritten."""
    from backend.config import settings
    from backend.services import purple_parts

    monkeypatch.setattr(settings, "purple_parts_url", "https://example.test")
    monkeypatch.setattr(settings, "purple_parts_api_key", "test-key")

    captured = {}

    async def fake_by_mpn(mpns, **kw):
        captured["mpns"] = list(mpns)
        return {
            "CL10A106KQ8NNNC": {
                "lcsc": "C1525",
                "mpn": "CL10A106KQ8NNNC",
                "manufacturer": "Samsung",
                "package": "0603",
                "description": "10uF 6.3V X5R 0603 MLCC",
                "category": "Capacitors",
                "subcategory": "MLCC - SMD/SMT",
            },
            "RC0603FR-0710KL": None,  # catalogue miss
        }

    monkeypatch.setattr(purple_parts, "lookup_mpn_batch", fake_by_mpn)

    client = _make_test_client(tmp_path)
    resp = client.post("/api/projects", json={"name": "realmpn"})
    project_id = resp.json()["id"]

    csv = (
        b"Reference,Value,Footprint,Manufacturer Part Number\n"
        b"U1,STM32,LQFP-48,STM32F103C8T6\n"
        b"C1,10uF,0603,CL10A106KQ8NNNC\n"
        b"R1,10k,0603,RC0603FR-0710KL\n"
        b"D1,Diode,SOD-123,1N4148WS\n"
    )
    resp = client.post(
        f"/api/projects/{project_id}/upload/bom",
        files={"file": ("bom.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lcsc_resolved"] == 0                       # no CSV rewrite
    assert body["lcsc_to_mpn"] == {"C1525": "CL10A106KQ8NNNC"}

    # Only passives are looked up (IC + simple excluded).
    assert sorted(captured["mpns"]) == sorted(
        ["CL10A106KQ8NNNC", "RC0603FR-0710KL"]
    )

    meta = client.get(f"/api/projects/{project_id}").json()
    assert set(meta["lcsc_payloads"].keys()) == {"C1525"}
    assert meta["lcsc_payloads"]["C1525"]["mpn"] == "CL10A106KQ8NNNC"
    assert meta["lcsc_to_mpn"] == {"C1525": "CL10A106KQ8NNNC"}
