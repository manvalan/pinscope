"""Finding schema — optional CAD/plugin fields, backward compatible.

Favor: legacy JSON still validates; new fields round-trip; pin-mux
fills rule_id + net + pins.
Against: invalid status rejected; pins must be a list; extra junk status
does not silently coerce.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.pinscopex.models import Finding


def test_legacy_json_without_new_fields_still_validates():
    raw = {
        "designator": "U3",
        "mpn": "MSPM0G3507SPTR",
        "finding": "Missing decoupling",
        "why": "Datasheet requires 100n close to VDD",
        "status": "ERROR",
        "reference": "p.12",
    }
    f = Finding.model_validate(raw)
    assert f.net is None
    assert f.pins == []
    assert f.rule_id is None
    assert f.cad_sheet is None
    assert f.cad_uuid is None
    assert f.variant is None


def test_new_fields_round_trip_json():
    f = Finding(
        designator="U1",
        mpn="SPX3819",
        finding="Cin too far",
        status="WARNING",
        net="VIN",
        pins=["U1.1", "C1.1"],
        rule_id="PS-DEC-001",
        cad_sheet="power.kicad_sch",
        cad_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        variant="DNP",
    )
    dumped = json.loads(f.model_dump_json())
    again = Finding.model_validate(dumped)
    assert again.net == "VIN"
    assert again.pins == ["U1.1", "C1.1"]
    assert again.rule_id == "PS-DEC-001"
    assert again.cad_sheet == "power.kicad_sch"
    assert again.cad_uuid == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert again.variant == "DNP"


def test_unknown_extra_keys_do_not_break_legacy_payloads():
    f = Finding.model_validate(
        {
            "designator": "R1",
            "finding": "ok",
            "status": "INFO",
            "future_field_from_old_report": True,
        }
    )
    assert f.designator == "R1"


def test_invalid_status_is_rejected():
    with pytest.raises(ValidationError):
        Finding(designator="U1", finding="x", status="error")


def test_pins_must_be_a_list_not_a_string():
    with pytest.raises(ValidationError):
        Finding(designator="U1", finding="x", status="INFO", pins="U1.1")


def test_status_ok_is_not_silently_accepted():
    with pytest.raises(ValidationError):
        Finding(designator="U1", finding="x", status="OK")
