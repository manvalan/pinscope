"""Pydantic models for PinscopeX: datasheet constraints and design graph."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, Tag, field_validator


class Pin(BaseModel):
    number: int | str
    name: str
    description: str | None = None
    functions: list[str] | None = None


class PackageInfo(BaseModel):
    base_family: str
    package: str
    pin_count: int
    description: str | None = None


class AbsMaxRating(BaseModel):
    parameter: str
    min: float | None = None
    max: float | None = None
    unit: str
    source_page: int


class Rule(BaseModel):
    rule_id: str | None = None  # {MPN}-{001}
    description: str
    source_page: int


def _check_subtype(v: object) -> str | None:
    """Shared pre-validator for component_subtype fields."""
    if v is None or v == "":
        return None
    from backend.pinscopex.taxonomy import validate_subtype
    return validate_subtype(str(v))


class ComponentConstraints(BaseModel):
    mpn: str
    model_version: str = "1.0.0"  # semver; bumped on prune (patch) or skill update (minor)
    component_subtype: str | None = None  # dotted taxonomy path, e.g. "ic.ldo", "ic.mcu"
    package_info: PackageInfo | None = None
    pintable: list[Pin]
    absolute_maximum_ratings: list[AbsMaxRating]
    rules: list[Rule]

    _validate_subtype = field_validator("component_subtype", mode="before")(
        staticmethod(_check_subtype)
    )

    def pin_by_number(self, number: int | str) -> Pin | None:
        """Look up a pin by its number."""
        for p in self.pintable:
            if str(p.number) == str(number):
                return p
        return None


# ---------------------------------------------------------------------------
# Design graph models
# ---------------------------------------------------------------------------


class NetType(str, Enum):
    POWER = "power"
    GROUND = "ground"
    SIGNAL = "signal"
    UNKNOWN = "unknown"


class ComponentType(str, Enum):
    RESISTOR = "resistor"
    CAPACITOR = "capacitor"
    INDUCTOR = "inductor"
    IC = "ic"
    CONNECTOR = "connector"
    CRYSTAL = "crystal"
    DISCRETE = "discrete"
    TRANSFORMER = "transformer"
    FUSE = "fuse"
    SWITCH = "switch"
    TEST_POINT = "test_point"
    FIDUCIAL = "fiducial"
    MECHANICAL = "mechanical"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Component specs taxonomy — type-specific, standardised-unit models
# ---------------------------------------------------------------------------


class ResistorSpecs(BaseModel):
    """Standardised resistor parameters. Value always in ohms."""
    specs_type: Literal["resistor"] = "resistor"
    component_subtype: str | None = None  # e.g. "passive.resistor"
    value_ohms: float
    value_formatted: str
    tolerance: str | None = None  # "±1%" or "±0.5ohm"
    package: str | None = None
    power_rating_w: str | None = None

    _validate_subtype = field_validator("component_subtype", mode="before")(
        staticmethod(_check_subtype)
    )


class CapacitorSpecs(BaseModel):
    """Standardised capacitor parameters. Value always in farads."""
    specs_type: Literal["capacitor"] = "capacitor"
    component_subtype: str | None = None  # e.g. "passive.capacitor.ceramic"
    value_farads: float
    value_formatted: str
    tolerance: str | None = None  # "±10%" or "±0.25pF"
    package: str | None = None
    voltage_rating_v: str | None = None
    dielectric: str | None = None

    _validate_subtype = field_validator("component_subtype", mode="before")(
        staticmethod(_check_subtype)
    )


class InductorSpecs(BaseModel):
    """Standardised inductor parameters. Value always in henries."""
    specs_type: Literal["inductor"] = "inductor"
    component_subtype: str | None = None  # e.g. "passive.inductor" or "passive.ferrite_bead"
    value_henries: float
    value_formatted: str
    tolerance: str | None = None  # "±5%" or "±0.1uH"
    package: str | None = None
    current_rating_a: str | None = None
    dcr_ohms: float | None = None

    _validate_subtype = field_validator("component_subtype", mode="before")(
        staticmethod(_check_subtype)
    )


class SimpleComponentSpecs(BaseModel):
    """Specs for discrete/simple components. Schema defined in taxonomy JSON."""
    specs_type: str  # taxonomy type: "discrete", "connector", "crystal", etc.
    component_subtype: str | None = None
    values: dict[str, float | str | None] = {}
    pintable: list[Pin] = []
    package_info: PackageInfo | None = None

    _validate_subtype = field_validator("component_subtype", mode="before")(
        staticmethod(_check_subtype)
    )

    def pin_by_number(self, number: int | str) -> Pin | None:
        """Look up a pin by its number."""
        for p in self.pintable:
            if str(p.number) == str(number):
                return p
        return None


def _specs_tag(v: Any) -> str:
    """Route to the correct specs model based on specs_type."""
    st = v.get("specs_type") if isinstance(v, dict) else v.specs_type
    return st if st in ("resistor", "capacitor", "inductor") else "simple"


ComponentSpecs = Annotated[
    Annotated[ResistorSpecs, Tag("resistor")]
    | Annotated[CapacitorSpecs, Tag("capacitor")]
    | Annotated[InductorSpecs, Tag("inductor")]
    | Annotated[SimpleComponentSpecs, Tag("simple")],
    Discriminator(_specs_tag),
]


class ComponentModel(BaseModel):
    """Persisted specs file — one per MPN in component-models/."""
    mpn: str
    specs: ComponentSpecs


# ---------------------------------------------------------------------------
# Design graph models
# ---------------------------------------------------------------------------


class PinConnection(BaseModel):
    """A pin on a component that participates in a net."""
    component_ref: str
    pin_number: str
    pin_name: str | None = None  # enriched from datasheet pintable


class Net(BaseModel):
    """An electrical net with mutable type/voltage for agent refinement."""
    name: str
    net_type: NetType = NetType.UNKNOWN
    voltage: float | None = None
    pins: list[PinConnection] = []


class Component(BaseModel):
    """A placed component in the design graph (topology only)."""
    reference: str
    value: str
    footprint: str
    component_type: ComponentType = ComponentType.UNKNOWN
    component_subtype: str | None = None  # dotted taxonomy path, e.g. "ic.ldo", "ic.mcu"
    mpn: str | None = None
    pins: dict[str, str] = {}  # pin_number -> net_name
    specs: ComponentSpecs | None = None

    _validate_subtype = field_validator("component_subtype", mode="before")(
        staticmethod(_check_subtype)
    )


class DesignGraph(BaseModel):
    """
    Bipartite design graph: Components <-> Nets.

    Traversal paths:
      component.pins[pin_num] -> net_name -> graph.nets[net_name].pins -> other components
      net.pins[i].component_ref -> graph.components[ref] -> its other pins/nets
    """
    components: dict[str, Component] = {}
    nets: dict[str, Net] = {}

    # -- Traversal helpers --------------------------------------------------

    def components_on_net(self, net_name: str) -> list[str]:
        """All component refs connected to a net."""
        net = self.nets.get(net_name)
        if not net:
            return []
        return list({pc.component_ref for pc in net.pins})

    def nets_of_component(self, ref: str) -> list[str]:
        """All net names a component touches."""
        comp = self.components.get(ref)
        if not comp:
            return []
        return list(set(comp.pins.values()))

    def neighbors(self, ref: str) -> dict[str, list[str]]:
        """Components sharing a net with *ref*, grouped by net name."""
        result: dict[str, list[str]] = {}
        for net_name in self.nets_of_component(ref):
            others = [r for r in self.components_on_net(net_name) if r != ref]
            if others:
                result[net_name] = others
        return result

    def components_by_type(self, comp_type: ComponentType) -> list[str]:
        """All refs matching a component type."""
        return [r for r, c in self.components.items() if c.component_type == comp_type]

    def power_nets(self) -> list[Net]:
        """All power and ground nets."""
        return [n for n in self.nets.values() if n.net_type in (NetType.POWER, NetType.GROUND)]

    def capacitors_on_net(self, net_name: str) -> list[str]:
        """Capacitor refs connected to a net (useful for decoupling checks)."""
        return [
            r for r in self.components_on_net(net_name)
            if self.components[r].component_type == ComponentType.CAPACITOR
        ]

    def components_by_subtype(self, prefix: str) -> list[str]:
        """All refs whose component_subtype starts with *prefix*.

        Examples:
            components_by_subtype("ic.power")       -> all power ICs
            components_by_subtype("passive.capacitor") -> all capacitors
            components_by_subtype("passive")          -> all passives
        """
        prefix_dot = prefix if prefix.endswith(".") else prefix + "."
        return [
            r for r, c in self.components.items()
            if c.component_subtype and (
                c.component_subtype == prefix
                or c.component_subtype.startswith(prefix_dot)
            )
        ]

    def pin_net(self, ref: str, pin_number: str) -> str | None:
        """Net name for a specific pin on a component."""
        comp = self.components.get(ref)
        if not comp:
            return None
        return comp.pins.get(pin_number)


# ---------------------------------------------------------------------------
# Validation report models
# ---------------------------------------------------------------------------


class Finding(BaseModel):
    """A single review finding — an issue found during direct datasheet review."""
    finding_id: str | None = None
    designator: str
    mpn: str = ""
    aspect: str | None = None       # "power_supply", "clock", etc. (for complex ICs)
    finding: str                     # What was observed in the actual circuit
    why: str = ""                    # Why it matters — from the datasheet
    source_page: int | None = None   # Datasheet page (null for deterministic checks)
    source_quote: str = ""           # Verbatim datasheet text supporting the finding (for PDF highlight)
    source_designator: str | None = None  # Designator whose datasheet source_page/source_quote refer to; None = this finding's own `designator`. Set when the evidence came from a connected component's datasheet excerpt (get_datasheet_excerpt), so the viewer opens the right PDF at the right page.
    status: Literal["ERROR", "WARNING", "INFO"]
    recommendation: str = ""
    reference: str = ""
    source: str | None = None        # None/"review" = LLM datasheet review; "pin_mux_check"/"led_current_check" = deterministic


class ValidationReport(BaseModel):
    """Full validation output."""
    project: str
    timestamp: str
    findings: list[Finding]
    summary: dict[str, int]
    coverage: dict[str, list[str]] = {}  # designator -> areas checked and found OK
    review_errors: dict[str, str] = {}   # designator -> error message for ICs whose review raised
    not_reviewed: list[dict] = []        # [{"designator","reason"}] — ICs skipped (e.g. no datasheet PDF)


class FindingComment(BaseModel):
    """A comment on a finding, stored outside the ValidationReport model."""
    comment_id: str
    finding_id: str
    user_id: str
    user_name: str
    text: str
    mentions: list[str] = []
    created_at: str


# ---------------------------------------------------------------------------
# Passive component pattern models
# ---------------------------------------------------------------------------


class PassiveFieldDef(BaseModel):
    """One named field in a passive component part number."""
    name: str
    position: int
    length: int
    description: str
    lookup: dict[str, str] = {}


class ValueDecoder(BaseModel):
    """How to decode the value field (resistance/capacitance) into a number.

    letter_multipliers maps characters to power-of-10 exponents (int) or the
    special string ``"decimal_point"`` for R-notation (e.g. 4R7 = 4.7 ohms).
    """
    type: str  # "eia3_pf" | "eia4_ohm_conditional"
    base_unit: str  # "pF" | "ohm"
    output_unit: str  # "F" | "ohm"
    letter_multipliers: dict[str, int | str] = {}
    zero_code: str | None = None
    conditional_on: dict | None = None


class PassivePattern(BaseModel):
    """Regex pattern + field decoders for a passive component family."""
    manufacturer: str
    series: str
    component_type: ComponentType
    component_subtype: str | None = None  # dotted taxonomy path, e.g. "passive.capacitor.ceramic"
    description: str
    regex: str
    fields: list[PassiveFieldDef]
    value_decoder: ValueDecoder
    example_mpns: list[str] = []
    datasheet_key: str | None = None  # library storage key for shared datasheet PDF

    _validate_subtype = field_validator("component_subtype", mode="before")(
        staticmethod(_check_subtype)
    )


class ResolvedPassive(BaseModel):
    """Result of resolving a BOM MPN against a stored pattern."""
    mpn: str
    references: list[str]
    component_type: ComponentType
    component_subtype: str | None = None  # dotted taxonomy path, e.g. "passive.resistor"

    _validate_subtype = field_validator("component_subtype", mode="before")(
        staticmethod(_check_subtype)
    )
    manufacturer: str
    series: str
    value: float
    value_formatted: str
    tolerance: str | None = None
    package: str | None = None
    voltage_rating: str | None = None
    power_rating: str | None = None
    dielectric: str | None = None
    raw_fields: dict[str, str] = {}
