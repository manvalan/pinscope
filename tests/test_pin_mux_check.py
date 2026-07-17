"""Pin-mux feasibility check — net-asserted peripheral function vs. the pin's
silicon alternate-function table.

Locks in:
1. A net asserting a function the pin can't be muxed to (UART5_TX on an RX-only
   pin) is a hard ERROR.
2. A correct assignment produces nothing.
3. DIRECTION is never flagged: an inter-device same-peripheral link (crossover /
   transceiver) is skipped, not flagged.
4. Empty functions / opaque nets are skipped.
5. Deterministic findings carry source="pin_mux_check" and never source_page.
"""

from __future__ import annotations

from backend.pinscopex.models import (
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
    Net,
    NetType,
    Pin,
    PinConnection,
    ValidationReport,
)
from backend.pinscopex.pin_function_tokens import normalize_functions, parse_net_token
from backend.pinscopex.pin_mux_check import check_pin_mux_feasibility


def _constraints(mpn, pintable):
    return ComponentConstraints(mpn=mpn, pintable=pintable,
                                absolute_maximum_ratings=[], rules=[])


def _ic(ref, mpn, pins):
    return Component(reference=ref, value="", footprint="",
                     component_type=ComponentType.IC, mpn=mpn, pins=pins)


def _graph(components, nets):
    """nets: {net_name: [(ref, pin_num), ...]}"""
    net_objs = {
        name: Net(name=name, net_type=NetType.SIGNAL,
                  pins=[PinConnection(component_ref=r, pin_number=str(p)) for r, p in conns])
        for name, conns in nets.items()
    }
    return DesignGraph(components=components, nets=net_objs)


# STM32-style: PD2 (pin 54) does UART5_RX only; PC12 (pin 53) does UART5_TX only.
_PD2 = Pin(number=54, name="PD2", functions=["TIM3_ETR", "UART5_RX", "EVENTOUT"])
_PC12 = Pin(number=53, name="PC12", functions=["SPI3_MOSI/I2S3_SDO", "UART5_TX"])


def test_real_defect_uart5_swapped_is_error():
    # Net labels assert TX on the RX-only pin and RX on the TX-only pin.
    u3 = _ic("U3", "MCUX", {"54": "MCU-UART5-TX", "53": "MCU-UART5-RX"})
    g = _graph({"U3": u3},
               {"MCU-UART5-TX": [("U3", 54)], "MCU-UART5-RX": [("U3", 53)]})
    cmap = {"MCUX": _constraints("MCUX", [_PD2, _PC12])}

    findings = check_pin_mux_feasibility(g, cmap)
    assert len(findings) == 2
    assert all(f.status == "ERROR" for f in findings)
    assert all(f.source == "pin_mux_check" for f in findings)
    assert all(f.source_page is None for f in findings)
    assert {f.designator for f in findings} == {"U3"}
    tx = next(f for f in findings if "MCU-UART5-TX" in f.finding)
    assert "cannot be muxed as UART5_TX" in tx.finding


def test_correct_assignment_no_finding():
    u3 = _ic("U3", "MCUX", {"54": "MCU-UART5-RX", "53": "MCU-UART5-TX"})
    g = _graph({"U3": u3},
               {"MCU-UART5-RX": [("U3", 54)], "MCU-UART5-TX": [("U3", 53)]})
    cmap = {"MCUX": _constraints("MCUX", [_PD2, _PC12])}
    assert check_pin_mux_feasibility(g, cmap) == []


def test_inter_device_same_peripheral_link_is_skipped():
    # A correct crossover: the net named from U3's TX perspective also lands on a
    # peer IC pin that exposes UART5. Direction is the reviewer's call -> skip.
    u3 = _ic("U3", "MCUX", {"54": "MCU-UART5-TX"})
    peer = _ic("U7", "PEER", {"5": "MCU-UART5-TX"})
    g = _graph({"U3": u3, "U7": peer},
               {"MCU-UART5-TX": [("U3", 54), ("U7", 5)]})
    cmap = {
        "MCUX": _constraints("MCUX", [_PD2]),
        "PEER": _constraints("PEER", [Pin(number=5, name="RXD", functions=["UART5_TX"])]),
    }
    assert check_pin_mux_feasibility(g, cmap) == []


def test_transceiver_peer_without_peripheral_still_fires():
    # Peer pin is a transceiver "DI" with no UART peripheral -> gate does NOT
    # apply; the MCU pin is still genuinely infeasible -> ERROR.
    u3 = _ic("U3", "MCUX", {"54": "MCU-UART5-TX"})
    xcvr = _ic("U9", "XCVR", {"1": "MCU-UART5-TX"})
    g = _graph({"U3": u3, "U9": xcvr},
               {"MCU-UART5-TX": [("U3", 54), ("U9", 1)]})
    cmap = {
        "MCUX": _constraints("MCUX", [_PD2]),
        "XCVR": _constraints("XCVR", [Pin(number=1, name="DI", functions=["DI"])]),
    }
    findings = check_pin_mux_feasibility(g, cmap)
    assert len(findings) == 1 and findings[0].status == "ERROR"


def test_empty_functions_skipped():
    u3 = _ic("U3", "MCUX", {"54": "MCU-UART5-TX"})
    g = _graph({"U3": u3}, {"MCU-UART5-TX": [("U3", 54)]})
    cmap = {"MCUX": _constraints("MCUX", [Pin(number=54, name="PD2", functions=None)])}
    assert check_pin_mux_feasibility(g, cmap) == []


def test_pin_exposes_peripheral_but_not_signal_no_complement():
    # Net asserts I2C1_SDA on a pin that exposes I2C1 only as SCL -> infeasible.
    u3 = _ic("U3", "MCUX", {"20": "I2C1-SDA-3V3"})
    g = _graph({"U3": u3}, {"I2C1-SDA-3V3": [("U3", 20)]})
    cmap = {"MCUX": _constraints("MCUX", [Pin(number=20, name="PB8", functions=["I2C1_SCL"])])}
    findings = check_pin_mux_feasibility(g, cmap)
    assert len(findings) == 1 and findings[0].status == "ERROR"


def test_opaque_net_not_flagged():
    u3 = _ic("U3", "MCUX", {"54": "NetC7_1"})
    g = _graph({"U3": u3}, {"NetC7_1": [("U3", 54)]})
    cmap = {"MCUX": _constraints("MCUX", [_PD2])}
    assert check_pin_mux_feasibility(g, cmap) == []


def test_token_parser_and_normalizer():
    assert parse_net_token("MCU-UART5-TX") == ("UART5", "TX")
    assert parse_net_token("I2C1-SDA-3V3") == ("I2C1", "SDA")
    assert parse_net_token("/UART0.TX") == ("UART0", "TX")
    assert parse_net_token("SPI2-CS") == ("SPI2", "NSS")   # CS canonicalises to NSS
    assert parse_net_token("NetC7_1") is None
    assert parse_net_token("+5V") is None
    assert ("UART5", "RX") in normalize_functions(["TIM3_ETR", "UART5_RX"])
    assert normalize_functions(["SPI3_MOSI/I2S3_SDO"]) >= {("SPI3", "MOSI")}


# TI MSPM0-style pintable: modern controller/peripheral SPI nomenclature.
# PB17 (pin 36) exposes SPI0 as PICO (== MOSI); PB19 (pin 38) as POCI (== MISO).
_PB17 = Pin(number=36, name="PB17", functions=["UART2_TX", "SPI0_PICO", "SPI1_CS1"])
_PB19 = Pin(number=38, name="PB19", functions=["SPI0_POCI", "UART0_CTS"])


def test_spi_legacy_net_names_match_modern_pin_functions():
    # Regression for the U3-001/U3-002 false positives: net labels use legacy
    # MOSI/MISO, the datasheet uses PICO/POCI — the same physical lines. No
    # finding: PICO≡MOSI, POCI≡MISO.
    u3 = _ic("U3", "MSPM0G3507SPTR", {"36": "/SPI0.MOSI", "38": "/SPI0.MISO"})
    g = _graph({"U3": u3},
               {"/SPI0.MOSI": [("U3", 36)], "/SPI0.MISO": [("U3", 38)]})
    cmap = {"MSPM0G3507SPTR": _constraints("MSPM0G3507SPTR", [_PB17, _PB19])}
    assert check_pin_mux_feasibility(g, cmap) == []


def test_spi_controller_peripheral_names_are_synonyms():
    assert parse_net_token("/SPI0.MOSI") == ("SPI0", "MOSI")
    assert parse_net_token("/SPI0.PICO") == ("SPI0", "MOSI")
    assert parse_net_token("SPI0-COPI") == ("SPI0", "MOSI")
    assert parse_net_token("/SPI0.MISO") == ("SPI0", "MISO")
    assert parse_net_token("/SPI0.POCI") == ("SPI0", "MISO")
    assert parse_net_token("SPI0-CIPO") == ("SPI0", "MISO")
    # Datasheet function strings collapse to the same canonical tokens.
    assert normalize_functions(["SPI0_PICO"]) == {("SPI0", "MOSI")}
    assert normalize_functions(["SPI0_POCI"]) == {("SPI0", "MISO")}
    # Indexed chip-select variants canonicalise to NSS.
    assert normalize_functions(["SPI0_CS0", "SPI1_CS3"]) == {
        ("SPI0", "NSS"), ("SPI1", "NSS")}
    assert normalize_functions(["SPI0_STE0"]) == {("SPI0", "NSS")}


def test_spi_genuine_infeasibility_still_fires_with_modern_names():
    # Net asserts SPI0_MOSI on a pin that exposes SPI0 only as POCI (==MISO) —
    # genuinely infeasible even after synonym collapse -> ERROR.
    u3 = _ic("U3", "MSPM0G3507SPTR", {"38": "/SPI0.MOSI"})
    g = _graph({"U3": u3}, {"/SPI0.MOSI": [("U3", 38)]})
    cmap = {"MSPM0G3507SPTR": _constraints("MSPM0G3507SPTR", [_PB19])}
    findings = check_pin_mux_feasibility(g, cmap)
    assert len(findings) == 1 and findings[0].status == "ERROR"
    # POCI==MISO is the complement of MOSI -> phrased as a likely swap.
    assert "swapped" in findings[0].why


def test_finding_prints_full_raw_capability_list_and_intent_caveat():
    # Net asserts I2C1_SDA on a pin that exposes I2C1 only as SCL -> infeasible.
    # The finding's `why` must (a) print the pin's full raw alternate-function
    # list verbatim, and (b) state the intent was inferred from the net name.
    pin = Pin(number=20, name="PB8", functions=["I2C1_SCL", "TIMA0_C1", "UART1_RX"])
    u3 = _ic("U3", "MCUX", {"20": "I2C1-SDA-3V3"})
    g = _graph({"U3": u3}, {"I2C1-SDA-3V3": [("U3", 20)]})
    cmap = {"MCUX": _constraints("MCUX", [pin])}
    f = check_pin_mux_feasibility(g, cmap)[0]
    # (a) every raw datasheet function string appears verbatim in `why`.
    for fn in ("I2C1_SCL", "TIMA0_C1", "UART1_RX"):
        assert fn in f.why
    # (b) the inferred-from-net-name caveat is present.
    assert "inferred from the net name" in f.why


def test_legacy_report_without_source_validates():
    # Backward-compat: a report.json from before these fields existed.
    legacy = {
        "finding_id": "U1-001", "designator": "U1", "mpn": "X",
        "finding": "f", "why": "w", "source_page": 3, "status": "WARNING",
    }
    f = Finding.model_validate(legacy)
    assert f.source is None
    rep = ValidationReport.model_validate({
        "project": "p", "timestamp": "t", "findings": [legacy],
        "summary": {"total": 1}, "coverage": {}, "review_errors": {},
    })
    assert rep.not_reviewed == []
