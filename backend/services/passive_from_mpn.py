"""Decode common chip R/C/L MPNs into typed specs without an LLM.

Only encodings that carry package + value (and voltage for capacitors when
the manufacturer puts it in the code) are accepted. Incomplete BOM-value
guesses stay out of the shared library.
"""

from __future__ import annotations

import re

from backend.pinscopex.models import ComponentModel, SimpleComponentSpecs
from backend.pinscopex.resolve_passives import simple_to_typed_passive_specs
from backend.services.passive_from_distributor import _spice

_SIZE = r"(?:0201|0402|0603|0805|1206|1210|1812|2010|2512)"
_DIEL = r"(?:C0G|NP0|CG|X8R|X7R|X7S|X6S|X5R|Y5V)"
_TOL = {
    "B": "±0.1%",
    "C": "±0.25%",
    "D": "±0.5%",
    "F": "±1%",
    "G": "±2%",
    "J": "±5%",
    "K": "±10%",
    "M": "±20%",
}
_AVX_V = {
    "4": "4V",
    "6": "6.3V",
    "Z": "10V",
    "Y": "16V",
    "3": "25V",
    "5": "50V",
    "1": "100V",
    "2": "200V",
    "7": "500V",
}
_AVX_DIEL = {"A": "C0G", "C": "X7R", "D": "X5R", "Z": "Y5V"}
_DIEL_NORM = {"CG": "C0G", "NP0": "C0G"}

_WALSIN_CAP = re.compile(
    rf"^({_SIZE})({_DIEL})(\d{{3}})([{''.join(_TOL)}])(\d{{3}}|[0-9]R[0-9])",
    re.IGNORECASE,
)
_AVX_CAP = re.compile(
    rf"^({_SIZE})([46ZY35127])([{''.join(_AVX_DIEL)}])(\d{{3}})([{''.join(_TOL)}])",
    re.IGNORECASE,
)
_CHIP_R = re.compile(
    rf"^(?:FRC)?({_SIZE})(?:W\d)?([{''.join('FJKG')}])(\d{{4}})",
    re.IGNORECASE,
)
# Murata LQW18AN: 0603 wirewound. Inductance is three chars (12N, 2N2, R10)
# then EIA tolerance, then a two-digit spec (00/10) and packing.
_LQW18AN = re.compile(
    r"^LQW18AN(?P<l>[0-9]N[0-9]|[0-9]{2}N|R[0-9]{2})(?P<tol>[BCSGHJKD])\d{2}",
    re.IGNORECASE,
)
_LQW_TOL = {
    "B": "±0.1nH",
    "C": "±0.2nH",
    "S": "±0.3nH",
    "D": "±0.5nH",
    "G": "±2%",
    "H": "±3%",
    "J": "±5%",
    "K": "±10%",
}


def _eia3_pf(digits: str) -> float:
    return float(int(digits[:2]) * (10 ** int(digits[2])))


def _eia3_volts(code: str) -> str | None:
    code = code.upper()
    if "R" in code:
        try:
            return f"{float(code.replace('R', '.')):g}V"
        except ValueError:
            return None
    if len(code) != 3 or not code.isdigit():
        return None
    volts = int(code[:2]) * (10 ** int(code[2]))
    return f"{volts}V"


def _eia4_ohm(digits: str) -> float:
    return float(int(digits[:3]) * (10 ** int(digits[3])))


def _lqw_nh(code: str) -> float | None:
    c = code.upper()
    if re.fullmatch(r"[0-9]N[0-9]", c):
        return float(f"{c[0]}.{c[2]}")
    if re.fullmatch(r"[0-9]{2}N", c):
        return float(c[:2])
    if re.fullmatch(r"R[0-9]{2}", c):
        return float(f"0.{c[1:]}") * 1000.0
    return None


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


def specs_from_mpn(mpn: str) -> ComponentModel | None:
    """Return a ComponentModel when the MPN itself encodes enough specs."""
    raw = (mpn or "").strip()
    if not raw:
        return None

    m = _WALSIN_CAP.match(raw)
    if m:
        size, diel, cap, tol, volt = m.groups()
        pf = _eia3_pf(cap)
        values = {
            "value_farads": _spice(str(pf), "p", "F") if pf else None,
            "package": size.upper(),
            "dielectric": _DIEL_NORM.get(diel.upper(), diel.upper()),
            "tolerance": _TOL[tol.upper()],
        }
        rated = _eia3_volts(volt)
        if rated:
            values["voltage_rating_v"] = rated
        values["value_formatted"] = values["value_farads"]
        if values["value_farads"]:
            return _model(raw, "passive.capacitor.ceramic", values)

    m = _AVX_CAP.match(raw)
    if m:
        size, vcode, diel, cap, tol = m.groups()
        pf = _eia3_pf(cap)
        values = {
            "value_farads": _spice(str(pf), "p", "F"),
            "value_formatted": _spice(str(pf), "p", "F"),
            "package": size.upper(),
            "dielectric": _AVX_DIEL[diel.upper()],
            "tolerance": _TOL[tol.upper()],
            "voltage_rating_v": _AVX_V[vcode.upper()],
        }
        return _model(raw, "passive.capacitor.ceramic", values)

    m = _CHIP_R.match(raw)
    if m:
        size, tol, code = m.groups()
        ohms = _eia4_ohm(code)
        values = {
            "value_ohms": _spice(str(ohms), None, "ohm"),
            "value_formatted": _spice(str(ohms), None, "ohm"),
            "package": size.upper(),
            "tolerance": _TOL[tol.upper()],
        }
        return _model(raw, "passive.resistor.thick_film", values)

    m = _LQW18AN.match(raw)
    if m:
        nh = _lqw_nh(m.group("l"))
        if nh is not None:
            henries = _spice(str(nh), "n", "H")
            values = {
                "value_henries": henries,
                "value_formatted": henries,
                "package": "0603",
                "tolerance": _LQW_TOL[m.group("tol").upper()],
            }
            return _model(raw, "passive.inductor", values)

    return None
