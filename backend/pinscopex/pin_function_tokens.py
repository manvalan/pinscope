"""Peripheral-function tokens parsed from net names and pin alternate-function
strings.

A *token* is a ``(peripheral, signal)`` pair, e.g. ``("UART5", "TX")`` or
``("I2C1", "SDA")``.  Both the schematic net name (user-authored, e.g.
``"MCU-UART5-TX"``) and the datasheet-extracted pin functions (e.g.
``"UART5_RX"``, ``"SPI3_MOSI/I2S3_SDO"``) are reduced to the same canonical
token space so they can be compared.

Used by:
  * ``pin_mux_check`` — the deterministic pin-mux feasibility check
  * ``validate.build_component_context`` — to render alt-functions only on
    peripheral-named-net pins (token-conscious context rendering)

Design goal is *high precision, low recall*: only emit a token when both the
bus family and the signal are unambiguous, so the feasibility check never
false-positives on opaque nets or vocabulary mismatches (CS vs NSS, TXD vs TX).
"""

from __future__ import annotations

import re

# Bus families whose pin assignment is muxed and whose naming is stable enough
# to validate.  Longer families that contain a shorter one as a substring
# (FDCAN/CAN, OCTOSPI/QSPI, USART/UART) are listed first; the patterns are
# anchored, so a token like "OCTOSPI1" never matches the bare "SPI" family.
_FAMILIES = (
    "LPUART", "USART", "UART", "I2C", "OCTOSPI", "QSPI", "SPI",
    "FDCAN", "CAN", "SDMMC", "SDIO", "I2S", "SAI", "USB",
)
_FAMILY_ALT = "|".join(_FAMILIES)

# A single net-name token that is exactly a bus family + optional instance number.
_PERIPHERAL_RE = re.compile(rf"^({_FAMILY_ALT})(\d*)$")
# A pin alternate-function string: <family><instance>_<signal...>.
_FUNCTION_RE = re.compile(rf"^({_FAMILY_ALT})(\d*)_(.+)$")

# Canonical signal names we compare on — restricted to signals with stable
# naming across user net labels and datasheet function strings.  SPI's
# controller/peripheral names (PICO/POCI/COPI/CIPO) are NOT canonical — they are
# synonyms of MOSI/MISO (same physical line, renamed) and collapse below.
_SIGNALS = {
    "TX", "RX", "SDA", "SCL", "MOSI", "MISO",
    "SCK", "NSS", "DP", "DM",
}

# Synonyms collapsed to a canonical signal before comparison.
_SIGNAL_SYNONYMS = {
    "TXD": "TX", "RXD": "RX",
    "SCLK": "SCK", "CLK": "SCK",
    "SS": "NSS", "CS": "NSS", "NCS": "NSS", "STE": "NSS",
    "DPLUS": "DP", "DMINUS": "DM",
    # SPI controller/peripheral nomenclature — the same physical lines as
    # master/slave MOSI/MISO, just renamed (TI/NXP/ST modern parts).  A net
    # labelled SPI0_MOSI landing on a pin whose datasheet function is SPI0_PICO
    # is feasible, not a defect.  (SDO/SDI deliberately omitted — their meaning
    # flips with controller-vs-peripheral perspective, so they aren't safe to
    # equate here.)
    "PICO": "MOSI", "COPI": "MOSI",
    "POCI": "MISO", "CIPO": "MISO",
}

# Directional complements — the signal that *should* be present if the asserted
# one isn't.  Used to phrase a feasibility finding as a likely swap.  Keyed on
# canonical signals only (PICO/POCI collapse to MOSI/MISO before this is read).
_COMPLEMENT = {
    "TX": "RX", "RX": "TX",
    "SDA": "SCL", "SCL": "SDA",
    "MOSI": "MISO", "MISO": "MOSI",
    "DP": "DM", "DM": "DP",
}

# Chip-select alternates often carry an instance suffix (SPI0_CS0..CS3, STE0..);
# strip the trailing index so every variant canonicalises to the bare CS token.
_CHIP_SELECT_INDEXED_RE = re.compile(r"^(N?CS|SS|STE)\d+$")


def _canon_signal(tok: str) -> str | None:
    """Canonicalise a raw signal token, or return None if it isn't a known signal."""
    t = tok.upper()
    m = _CHIP_SELECT_INDEXED_RE.match(t)
    if m:
        t = m.group(1)
    t = _SIGNAL_SYNONYMS.get(t, t)
    return t if t in _SIGNALS else None


def _tokens(name: str) -> list[str]:
    """Split a net name into delimiter-separated tokens (uppercased)."""
    s = name.upper().lstrip("/")
    # Map the only signals that embed a delimiter char before splitting.
    s = s.replace("D+", "DP").replace("D-", "DM")
    s = re.sub(r"[._/]", "-", s)
    return [p for p in s.split("-") if p]


def parse_net_token(net_name: str) -> tuple[str, str] | None:
    """Extract a ``(peripheral, canonical_signal)`` token from a net name, or None.

    Emits only when a bus-family token is immediately followed by a known
    signal, e.g. ``"MCU-UART5-TX" -> ("UART5", "TX")``,
    ``"I2C1-SDA-3V3" -> ("I2C1", "SDA")``.  Opaque nets (``"NetC7_1"``,
    ``"MCU-RESET"``) return None.
    """
    parts = _tokens(net_name)
    for i in range(len(parts) - 1):
        m = _PERIPHERAL_RE.match(parts[i])
        if not m:
            continue
        sig = _canon_signal(parts[i + 1])
        if sig is None:
            continue
        return (m.group(1) + m.group(2), sig)
    return None


def normalize_functions(functions: list[str] | None) -> set[tuple[str, str]]:
    """Reduce a pin's alternate-function strings to canonical
    ``(peripheral, signal)`` tokens.  Splits slash-joined alternates
    (``"SPI3_MOSI/I2S3_SDO"`` -> two tokens)."""
    out: set[tuple[str, str]] = set()
    for f in functions or []:
        for alt in f.upper().replace("D+", "DP").replace("D-", "DM").split("/"):
            m = _FUNCTION_RE.match(alt.strip())
            if not m:
                continue
            sig = _canon_signal(m.group(3))
            if sig is None:
                continue
            out.add((m.group(1) + m.group(2), sig))
    return out


def signals_for_peripheral(funcs: set[tuple[str, str]], peripheral: str) -> set[str]:
    """All canonical signals a function set exposes for one peripheral instance."""
    return {s for (p, s) in funcs if p == peripheral}


def complement(signal: str) -> str | None:
    """The directional complement of a signal (TX<->RX, SDA<->SCL, ...), or None."""
    return _COMPLEMENT.get(signal)
