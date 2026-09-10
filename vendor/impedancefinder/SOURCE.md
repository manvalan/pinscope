Vendored snapshot of https://github.com/manvalan/ImpedenceFinder

Commit: a0c8d0ec37c9a1b099082926e50a245778ec8d6e

Included: closed-form core (`zsolver`, `geometry`, `planes`, `model`,
`net_walk`, `net_analysis`, `report`).

Excluded on purpose:
- `gerber2ems_export.py`, `prepare_simulation.py`, `crop_board.py`,
  `simulate_net.sh` (OpenEMS / field-solver export)
- `board_model.py` and `plugin/` (pcbnew). Pinscope does not load KiCad's
  Python; PCB ingest stays in `pinscopex.parsers_kicad_pcb`.
