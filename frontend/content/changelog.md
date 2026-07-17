# Changelog

What's new in Pinscope.

## 2.6.0 — 2026-07-12 — Export Report to Excel

Download a project's findings as an Excel spreadsheet straight from the report — one click, ready to share, filter, or archive outside Pinscope.

- [New] "Export Excel" button on the validation report. Every finding becomes a spreadsheet row — designator, part number, ID, severity, title, description, recommendation, and its datasheet source (page included) — sorted most-severe first.

## 2.5.1 — 2026-07-04 — More Thorough Reviews

Schematic review now works through every functional area of a component before finishing, so a part with several independent issues has all of them surfaced in one pass instead of just the first.

- [Improved] For each IC, the review covers power and decoupling, every signal interface, absolute-maximum ratings, and reset/boot/configuration and unused pins before reporting — catching multiple issues on the same component that could previously be missed.

## 2.5.0 — 2026-07-02 — Light Mode

Pinscope now has a light theme. Toggle between light and dark with the sun/moon button — in the sidebar next to your account menu, or in the header on the website.

- [New] Theme toggle. Switch between light and dark mode anywhere in the app; your choice is remembered on this device. Everything defaults to dark, exactly as before, until you flip it.
- [Improved] Every status color — error, warning, and pass badges, finding cards, the progress view, billing — is tuned for both themes, so reports stay legible either way.
- [Improved] The sign-in page and account menu now follow the app theme instead of always rendering light.

## 2.4.0 — 2026-07-01 — Automatic Pin & LED Current Checks

Two datasheet-grounded checks now run on every project, independent of the schematic review — catching a swapped-peripheral pin or an over-driven LED — plus a clear list of any components that had no datasheet to review against.

- [New] Pin-function feasibility check. Pinscope now flags when a net assigns an IC pin a peripheral function its silicon can't route — for example a `UART5_TX` net on a pin whose alternate-function table only offers `UART5_RX`. It's reported as an error straight from the datasheet's pin table and names the likely swap (TX↔RX, SDA↔SCL). It deliberately does not judge signal *direction* across an interface — a direct UART crosses TX↔RX while a transceiver runs straight through — so it only fires on physically impossible pin assignments, never on wiring style.
- [New] LED forward-current check. For each LED, Pinscope computes the forward current from the supply rail, the series resistor, and the LED's rated forward voltage, and flags any channel whose current exceeds the LED's rated maximum. Each color of an RGB LED is checked separately, and a leg with no current-limiting resistor at all is called out as a caution.
- [New] "Not reviewed" list on the report. Components with no datasheet on file — for instance a do-not-populate footprint that isn't in the BOM — are now called out explicitly, so a mis-wired pin on an unreviewed part shows up as a known gap instead of being silently absent.
- [New] Findings from these automatic checks carry an "Automated check" badge, so they're easy to tell apart from datasheet-review findings.

## 2.3.3 — 2026-06-08 — Faster Reviews

Multi-chip designs now review several times faster — Pinscope works through ICs in parallel instead of one at a time.

- [Improved] Datasheet extraction and schematic review now process multiple ICs at once, so reports on multi-IC projects come back substantially faster. The findings are unchanged — only the wait is shorter.

## 2.3.2 — 2026-05-26 — Smarter RF Topology Review

Schematic review now reasons about *what each external part is for* before flagging it — catching valid bias, coupling, and matching circuits that previously looked like errors.

- [Improved] The reviewer states the role of every external part on an IC pin (choke, blocking cap, divider, decoupling, matching) before judging the connection. Common RF topologies like bias-T (DC injected onto a coax through a choke, with the chip protected by an internal DC block and a downstream load doing the actual draw) are no longer flagged as errors against the chip.
- [Improved] Stricter absolute-maximum-rating checks: the cited limit must come from the same pin under stress (a Vdd abs-max no longer counts against an RF or signal pin), and the inequality must be a strict exceed — equal-to-abs-max is at most a Warning.
- [Improved] Single-concern deep dives are capped at two follow-up queries; concerns that can't be resolved in that budget are reported as Warnings with the unresolved question stated, so one suspect finding can't starve the rest of the IC review.
- [Improved] Inferred rail voltages from the power-tree pass are no longer treated as ground truth by the schematic reviewer. Voltages set by net name (`+5V`, `+3V3`) or by user power-source hints are trusted as before; voltages the power-tree LLM guessed for an adjustable regulator output or propagated through inference are kept on the power-tree view for reference but excluded from review reasoning, so a single misread rail can't anchor a false-positive Error.
- [Improved] After each IC's review, a second pass normalizes findings against a fixed Error/Warning/Info rubric and merges any two findings that share a single root cause (e.g. "series resistor drops VIN" and "VOUT setpoint exceeds available VIN" are one defect, not two). Cuts run-to-run severity drift and avoids inflating the error count when one defect can be described from multiple angles.

## 2.3.1 — 2026-05-25 — EDIF Netlist Support

EDIF 2.0.0 netlists upload alongside PADS-PCB, with a sub-design picker for files that contain more than one design. Schematic review is also more cautious about polarity / direction-control findings.

- [New] Upload EDIF 2.0.0 (`.edn`) netlists directly. Format is auto-detected from the file contents — no need to convert to PADS-PCB first. Verified against Siemens xDX Designer exports.
- [New] When an EDIF file contains multiple sub-designs, project setup shows a picker so you can choose which one to review. The picker auto-confirms when there's a single clean match against your BOM and only asks when it's ambiguous; unselected sub-designs are filtered out of the design graph.
- [Improved] Stronger verification of differential and polarity pin assignments (USB D+/D−, TX/RX, IN+/IN−, anode/cathode) directly against the datasheet.
- [Improved] Improved support for bidirectional buffers and level translators (74xx245 and friends) — direction-control truth tables are factored into bus-contention analysis.
- [Improved] Findings that share a single root cause on the same chip are grouped into one combined finding.

## 2.3.0 — 2026-05-24 — LCSC Part Number Support

JLCPCB-style BOMs with LCSC part numbers (e.g. `C12044`) now work out of the box — Pinscope auto-detects the column, resolves each id to the real manufacturer part number, and shows you what it resolved to before the pipeline runs.

- [New] LCSC part numbers in the manufacturer part number column are auto-detected at BOM upload and converted to real MPNs. Works with JLCPCB / EasyEDA exports without any column renaming.
- [New] Project setup now shows the LCSC → MPN mapping on each IC row in the datasheet step (e.g. `C12044 → TP4057-42-SOT26-R`), so you can see what each LCSC id became before the pipeline starts.
- [New] Passive specs (value, voltage, tolerance, dielectric, package) are resolved from the LCSC catalog during project setup, with per-row progress and status — you see what's resolved before spending credits on the full pipeline.
- [Improved] Datasheet auto-fetch hit rate is dramatically higher on LCSC BOMs, because DigiKey now sees real MPNs instead of `C…` ids.

## 2.2.1 — 2026-05-22 — Easier Netlist Uploads & Xpedition Support

Tabbed file upload guide with per-tool instructions, Xpedition coverage, and direct `.net` / `.txt` uploads.

- [New] Documentation for exporting a PADS-PCB netlist from Siemens Xpedition Designer / DxDesigner (VX.2.x, including VX.2.14).
- [Improved] File upload guide reorganized into tabs — KiCad, Altium, OrCAD/Allegro, Xpedition, EasyEDA, and Eagle each get their own panel.
- [Improved] Netlist uploads now accept `.asc`, `.net`, `.NET`, and `.txt` directly — no more renaming required before upload.
- [Improved] File guide now calls out the difference between the PADS-PCB schematic netlist Pinscope needs and the `!PADS-POWERPCB` PCB-layout dump that some EDA tools also save as `.asc`.

## 2.2.0 — 2026-05-20 — Cross-chip Datasheet Review

The reviewer now reads neighbor-chip datasheets to verify cross-chip constraints, with fewer false errors when a spec can't be confirmed.

- [Improved] Schematic review now cross-references connected chips: when an issue depends on a neighbor's spec (5V tolerance, absolute-max, drive strength), the reviewer pulls the relevant pages from that chip's datasheet before flagging it.
- [Improved] Fewer false errors on cross-chip findings: if a counterpart spec can't be confirmed from the datasheet, the issue is reported as a Warning with the unverified assumption stated — instead of being overstated as an Error.
- [Fixed] Some review findings could occasionally fail to appear in the report.

## 2.1.0 — 2026-05-19 — Datasheet Reference Highlighting

Datasheet citations now highlight the exact supporting sentence on the PDF page, with more reliable page numbers on large datasheets.

- [Improved] Datasheet references now highlight the exact supporting sentence on the PDF page, not just the page number.
- [Fixed] Datasheet citations landing on the wrong page for large (multi-hundred-page) datasheets.
- [Fixed] Reviewed findings losing their checked state on page refresh.

## 2.0.1 — 2026-05-01 — Flagging & Onboarding

One-click flags on finding cards, an onboarding survey for new users, and small UI polish.

- [New] Report findings with one click via the flag button on any finding card.
- [New] Onboarding survey for new users to help us improve the product.
- [Improved] Comment input box now fills available width.

## 2.0.0 — 2026-04-29 — Public Changelog

- [New] Initial public changelog.
