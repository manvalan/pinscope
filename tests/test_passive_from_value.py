import asyncio
from pathlib import Path

import pytest

from backend.services.passive_from_value import (
    is_placeholder_value,
    specs_from_bom_value,
)


def test_capacitor_picofarads():
    model = specs_from_bom_value("18pF", "18pF", "C")
    assert model is not None
    assert model.specs.specs_type == "capacitor"
    assert abs(model.specs.value_farads - 18e-12) < 1e-18


def test_resistor_kilo_and_euro():
    k = specs_from_bom_value("4.7k", "4.7k", "R")
    assert k is not None
    assert abs(k.specs.value_ohms - 4700) < 1e-6
    euro = specs_from_bom_value("4k7", "4k7", "R")
    assert euro is not None
    assert abs(euro.specs.value_ohms - 4700) < 1e-6


def test_inductor_uh():
    model = specs_from_bom_value("10uH", "10uH", "L")
    assert model is not None
    assert abs(model.specs.value_henries - 10e-6) < 1e-12


def test_ferrite_impedance_at_freq():
    model = specs_from_bom_value("600R@100MHz", "600R@100MHz", "FB")
    assert model is not None
    assert model.specs.component_subtype == "passive.ferrite_bead"
    assert model.specs.impedance_ohm == 600
    assert model.specs.value_henries is None
    assert "100MHz" in model.specs.value_formatted


def test_placeholders_skip():
    for raw in ("DNP", "NC", "JUMPER", "TBD", "-"):
        assert is_placeholder_value(raw)
        assert specs_from_bom_value(raw, raw, "R") is None


def test_ambiguous_string_returns_none():
    assert specs_from_bom_value("mystery", "do not stuff", "R") is None


def test_resolve_from_value_skips_llm_when_parseable(monkeypatch):
    from backend.services.extraction import resolve_from_value

    async def boom(*_a, **_k):
        raise AssertionError("LLM must not run")

    monkeypatch.setattr("backend.services.extraction.call_with_fallback", boom)
    tax = Path(__file__).resolve().parents[1] / "taxonomy"
    model = asyncio.run(
        resolve_from_value(
            mpn="18pF",
            value="18pF",
            ref_prefix="C",
            taxonomy_dir=tax,
        )
    )
    assert abs(model.specs.value_farads - 18e-12) < 1e-18


def test_resolve_from_value_placeholder_no_llm(monkeypatch):
    from backend.services.extraction import resolve_from_value

    async def boom(*_a, **_k):
        raise AssertionError("LLM must not run")

    monkeypatch.setattr("backend.services.extraction.call_with_fallback", boom)
    tax = Path(__file__).resolve().parents[1] / "taxonomy"
    with pytest.raises(ValueError, match="Placeholder"):
        asyncio.run(
            resolve_from_value(
                mpn="DNP",
                value="DNP",
                ref_prefix="R",
                taxonomy_dir=tax,
            )
        )
