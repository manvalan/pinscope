"""PADS-PCB netlist parser — section-marker robustness.

Regression coverage for EasyEDA Pro exports, which decorate section headers
with trailing labels (``*PART*       ITEMS``) and append a ``*MISC*
ATTRIBUTE VALUES`` block after the connectivity. Earlier versions did
exact-string matching on section markers, so:

  1. The decorated ``*PART*`` header was not recognised — no real parts
     were collected.
  2. The unrecognised ``*MISC*`` block leaked into the net section, where
     ``"Datasheet" https://...`` and ``"Footprint" C0603_L1.6-W0.8-H0.8``
     tokens were misparsed as ``ref.pin`` pin connections, polluting the
     graph with phantom components.
"""

from __future__ import annotations

from backend.pinscopex.parsers import parse_netlist


EASYEDA_PRO_NETLIST = """\
!PADS-POWERPCB-V9.0-MILS-CP936! Created by EasyEDA Pro V2.2.47.7
*REMARK* Smart Gas Cap_1 -- 2026-04-25 14:52:03
*REMARK*

*PART*       ITEMS
U1      RP2040@LQFN-56_L7.0-W7.0-P0.4-EP
C1      CAI0603X7R105K250JT@C0603
R1      FRC0603J105 TS@R0603
*NET*
*SIGNAL* GND
U1.19 C1.1 R1.1
*SIGNAL* +3V3
U1.1 C1.2 R1.2

*MISC*      MISCELLANEOUS PARAMETERS

ATTRIBUTE VALUES
{
PART U1
{
"Manufacturer Part" RP2040
"Datasheet" https://item.szlcsc.com/datasheet/RP2040/2392.html
"Footprint" LQFN-56_L7.0-W7.0-P0.4-EP
"3D Model Title" LQFN-56_L7.0-W7.0-P0.4-EP
"Description" Voltage Range:2.7V~3.6V Current:100mA
}
PART C1
{
"Datasheet" https://item.szlcsc.com/datasheet/CAI0603X7R105K250JT/51675802.html
"3D Model Title" C0603_L1.6-W0.8-H0.8
}
}
*END*     OF ASCII OUTPUT FILE
"""


def test_easyeda_pro_misc_section_does_not_pollute_nets(tmp_path):
    """*MISC* attribute block must not leak into net connectivity."""
    netlist_path = tmp_path / "netlist.asc"
    netlist_path.write_text(EASYEDA_PRO_NETLIST)

    known_refs = {"U1", "C1", "R1"}
    parts, nets = parse_netlist(netlist_path, known_refs=known_refs)

    assert set(parts.keys()) == {"U1", "C1", "R1"}, (
        f"phantom parts leaked from *MISC* block: "
        f"{set(parts.keys()) - {'U1', 'C1', 'R1'}}"
    )
    assert set(nets.keys()) == {"GND", "+3V3"}

    all_refs = {ref for pin_list in nets.values() for ref, _ in pin_list}
    assert all_refs <= known_refs, (
        f"phantom refs in nets from *MISC* misparse: {all_refs - known_refs}"
    )


def test_decorated_part_header_is_recognised(tmp_path):
    """``*PART*       ITEMS`` (with trailing label) must enter the part section."""
    netlist_path = tmp_path / "netlist.asc"
    netlist_path.write_text(
        "*PART*       ITEMS\n"
        "U1      RP2040@LQFN-56\n"
        "C1      CAP@C0603\n"
        "*NET*\n"
        "*SIGNAL* GND\n"
        "U1.19 C1.1\n"
        "*END*\n"
    )

    parts, nets = parse_netlist(netlist_path)

    assert parts == {"U1": "RP2040@LQFN-56", "C1": "CAP@C0603"}
    assert nets == {"GND": [("U1", "19"), ("C1", "1")]}


def test_part_section_without_trailing_label_still_parses(tmp_path):
    """Plain ``*PART*`` header (no trailing label) must still be recognised."""
    netlist_path = tmp_path / "netlist.asc"
    netlist_path.write_text(
        "*PADS-PCB*\n"
        "*PART*\n"
        "U1 RP2040@LQFN-56\n"
        "*NET*\n"
        "*SIGNAL* GND\n"
        "U1.1\n"
        "*END*\n"
    )

    parts, nets = parse_netlist(netlist_path)

    assert parts == {"U1": "RP2040@LQFN-56"}
    assert nets == {"GND": [("U1", "1")]}
