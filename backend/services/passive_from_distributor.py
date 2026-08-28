"""Map distributor parameters / LCSC descriptions to typed passive specs.

Used so capacitor / resistor / inductor rows skip the LLM when the catalog
already states value, voltage, tolerance, and package.
"""

from __future__ import annotations

import re

from backend.pinscopex.models import ComponentModel, SimpleComponentSpecs
from backend.pinscopex.resolve_passives import simple_to_typed_passive_specs

_CAP = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<mul>[pnuμµmk])?\s*[fF]\b",
)
_RES = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<mul>[pnuμµmkM])?\s*(?:ohms?|Ω|R)\b",
    re.IGNORECASE,
)
_IND = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<mul>[pnuμµmk])?\s*H\b",
)
_TOL = re.compile(r"±\s*(?P<num>\d+(?:\.\d+)?)\s*%")
_VOLT = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*V\b")
_PKG = re.compile(r"\b(?P<pkg>0201|0402|0603|0805|1206|1210|1812|2220|2512)\b")
_DIEL = re.compile(r"\b(?P<diel>C0G|NP0|X5R|X6S|X7R|X7S|X8R|Y5V|Z5U)\b", re.I)

_MUL = {
    "p": 1e-12, "n": 1e-9, "u": 1e-6, "μ": 1e-6, "µ": 1e-6,
    "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6,
}


def _spice(num: str, mul: str | None, unit: str) -> str:
    n = float(num)
    factor = _MUL.get((mul or ""), 1.0)
    value = n * factor
    if unit == "F":
        if value >= 1e-6:
            return f"{value * 1e6:g}uF"
        if value >= 1e-9:
            return f"{value * 1e9:g}nF"
        return f"{value * 1e12:g}pF"
    if unit == "ohm":
        if value >= 1e6:
            return f"{value / 1e6:g}Mohm"
        if value >= 1e3:
            return f"{value / 1e3:g}kohm"
        return f"{value:g}ohm"
    if unit == "H":
        if value >= 1e-3:
            return f"{value * 1e3:g}mH"
        if value >= 1e-6:
            return f"{value * 1e6:g}uH"
        return f"{value * 1e9:g}nH"
    return f"{value:g}{unit}"


def _param_map(params: list[dict[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in params:
        name = (p.get("name") or "").strip().lower()
        value = (p.get("value") or "").strip()
        if name and value:
            out[name] = value
    return out


def _first(pmap: dict[str, str], *needles: str) -> str | None:
    for key, val in pmap.items():
        for needle in needles:
            if needle in key:
                return val
    return None


def _classify(category: str, description: str, pmap: dict[str, str]) -> str | None:
    blob = f"{category} {description} {' '.join(pmap.values())}".lower()
    if "ferrite" in blob or "bead" in blob:
        return None  # typed model wants henries; leave to the LLM
    if "capacitor" in blob or "mlcc" in blob or "ceramic" in blob:
        diel = _DIEL.search(description) or _DIEL.search(" ".join(pmap.values()))
        if diel or "ceramic" in blob or "mlcc" in blob:
            return "passive.capacitor.ceramic"
        if "tantalum" in blob:
            return "passive.capacitor.tantalum"
        if "electrolytic" in blob or "aluminum" in blob:
            return "passive.capacitor.electrolytic"
        return "passive.capacitor.ceramic"
    if "resistor" in blob:
        if "thin film" in blob:
            return "passive.resistor.thin_film"
        if "thick film" in blob:
            return "passive.resistor.thick_film"
        return "passive.resistor"
    if "inductor" in blob or "choke" in blob:
        return "passive.inductor"
    return None


def specs_from_distributor(
    *,
    mpn: str,
    params: list[dict[str, str]],
    category: str,
    description: str,
) -> ComponentModel | None:
    """Return a ComponentModel when value + type can be parsed without an LLM."""
    pmap = _param_map(params)
    text = " ".join(
        [description, category, *pmap.values()],
    )
    subtype = _classify(category, description, pmap)
    if not subtype:
        # Infer from parsed units in the description alone
        if _CAP.search(text) and not _RES.search(text):
            subtype = "passive.capacitor.ceramic"
        elif _RES.search(text) and "capacitor" not in text.lower():
            subtype = "passive.resistor"
        elif _IND.search(text):
            subtype = "passive.inductor"
        else:
            return None

    values: dict[str, str] = {}
    cap = _first(pmap, "capacitance") or (
        _CAP.search(text).group(0) if _CAP.search(text) else None
    )
    res = _first(pmap, "resistance") or (
        _RES.search(text).group(0) if _RES.search(text) else None
    )
    ind = _first(pmap, "inductance") or (
        _IND.search(text).group(0) if _IND.search(text) else None
    )

    if subtype.startswith("passive.capacitor"):
        if not cap:
            return None
        m = _CAP.search(cap) or _CAP.search(text)
        if not m:
            return None
        values["value_farads"] = _spice(m.group("num"), m.group("mul"), "F")
        values["value_formatted"] = values["value_farads"]
        volt = _first(pmap, "voltage") or (
            f"{_VOLT.search(text).group('num')}V" if _VOLT.search(text) else None
        )
        if volt:
            values["voltage_rating_v"] = volt if volt.lower().endswith("v") else f"{volt}V"
        diel = _first(pmap, "temperature coefficient", "dielectric")
        dm = _DIEL.search(diel or "") or _DIEL.search(text)
        if dm:
            values["dielectric"] = dm.group("diel").upper().replace("NP0", "C0G")
    elif subtype.startswith("passive.resistor"):
        if not res:
            return None
        m = _RES.search(res) or _RES.search(text)
        if not m:
            return None
        values["value_ohms"] = _spice(m.group("num"), m.group("mul"), "ohm")
        values["value_formatted"] = values["value_ohms"]
        power = _first(pmap, "power")
        if power:
            values["power_rating_w"] = power
    elif subtype.startswith("passive.inductor"):
        if not ind:
            return None
        m = _IND.search(ind) or _IND.search(text)
        if not m:
            return None
        values["value_henries"] = _spice(m.group("num"), m.group("mul"), "H")
        values["value_formatted"] = values["value_henries"]
    else:
        return None

    tol = _first(pmap, "tolerance")
    tm = _TOL.search(tol or "") or _TOL.search(text)
    if tm:
        values["tolerance"] = f"±{tm.group('num')}%"
    elif tol:
        values["tolerance"] = tol

    pkg = _first(pmap, "package", "case", "size")
    pm = _PKG.search(pkg or "") or _PKG.search(text)
    if pm:
        values["package"] = pm.group("pkg")
    elif pkg and len(pkg) <= 12:
        values["package"] = pkg

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
