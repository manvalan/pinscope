"""Parse a BOM Value string into typed passive specs without an LLM."""

from __future__ import annotations

import re

from backend.pinscopex.models import ComponentModel, SimpleComponentSpecs
from backend.pinscopex.resolve_passives import simple_to_typed_passive_specs
from backend.services.passive_from_distributor import _spice

_PLACEHOLDER = re.compile(
    r"^(?:dnp|dni|dns|nc|n/?c|n/?a|np|nfs|tbd|todo|jumper|jmp|short|open|-|—|–)$",
    re.I,
)

_CAP = re.compile(
    r"^(?P<num>\d+(?:\.\d+)?)\s*(?P<mul>[pnuμµmk])?\s*[fF]$",
)
_IND = re.compile(
    r"^(?P<num>\d+(?:\.\d+)?)\s*(?P<mul>[pnuμµmk])?\s*H$",
    re.I,
)
_RES_UNIT = re.compile(
    r"^(?P<num>\d+(?:\.\d+)?)\s*(?P<mul>[pnuμµmkM])?\s*(?:ohms?|Ω|R)$",
    re.I,
)
_RES_BARE_MUL = re.compile(
    r"^(?P<num>\d+(?:\.\d+)?)(?P<mul>[kKmM])$",
)
_EURO_R = re.compile(
    r"^(?P<a>\d+)[kK](?P<b>\d+)$",
)
_FB = re.compile(
    r"^(?P<num>\d+(?:\.\d+)?)\s*(?P<mul>[kKmM])?\s*(?:ohms?|Ω|R)"
    r"(?:@\s*(?P<freq>\d+(?:\.\d+)?\s*(?:kHz|MHz|GHz|Hz)))?$",
    re.I,
)


def is_placeholder_value(value: str) -> bool:
    return bool(_PLACEHOLDER.match((value or "").strip()))


def _model(mpn: str, subtype: str, values: dict[str, str]) -> ComponentModel | None:
    specs = SimpleComponentSpecs(
        specs_type="passive",
        component_subtype=subtype,
        values=values,
    )
    try:
        typed = simple_to_typed_passive_specs(specs)
    except (ValueError, TypeError):
        return None
    return ComponentModel(mpn=mpn, specs=typed)


def specs_from_bom_value(
    mpn: str, value: str, ref_prefix: str,
) -> ComponentModel | None:
    """Map ``18pF`` / ``4.7k`` / ``10uH`` / ``600R@100MHz`` to typed specs.

    Returns None for placeholders (DNP, NC, JUMPER) and for strings that
    are not a single passive value. Callers must not save the result to the
    shared library.
    """
    raw = (value or "").strip()
    prefix = (ref_prefix or "").upper()
    if not raw or is_placeholder_value(raw):
        return None

    compact = re.sub(r"\s+", "", raw)

    if prefix == "FB" or "@" in compact:
        m = _FB.match(compact) or _FB.match(raw)
        if m:
            z = _spice(m.group("num"), m.group("mul"), "ohm")
            freq = re.sub(r"\s+", "", m.group("freq") or "")
            values = {
                "impedance_ohm": z,
                "value_formatted": f"{z}@{freq}" if freq else z,
            }
            return _model(mpn, "passive.ferrite_bead", values)

    if prefix in {"C", ""}:
        m = _CAP.match(compact) or _CAP.match(raw)
        if m:
            farads = _spice(m.group("num"), m.group("mul"), "F")
            return _model(mpn, "passive.capacitor", {
                "value_farads": farads,
                "value_formatted": farads,
            })

    if prefix in {"L", ""}:
        m = _IND.match(compact) or _IND.match(raw)
        if m:
            henries = _spice(m.group("num"), m.group("mul"), "H")
            return _model(mpn, "passive.inductor", {
                "value_henries": henries,
                "value_formatted": henries,
            })

    if prefix in {"R", ""}:
        m = _EURO_R.match(compact)
        if m:
            ohms = float(f"{m.group('a')}.{m.group('b')}") * 1e3
            formatted = _spice(str(ohms), None, "ohm")
            return _model(mpn, "passive.resistor", {
                "value_ohms": formatted,
                "value_formatted": formatted,
            })
        m = _RES_UNIT.match(compact) or _RES_BARE_MUL.match(compact)
        if m:
            formatted = _spice(m.group("num"), m.group("mul"), "ohm")
            return _model(mpn, "passive.resistor", {
                "value_ohms": formatted,
                "value_formatted": formatted,
            })

    return None
