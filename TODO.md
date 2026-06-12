# TODO — PipetteLiquidHandler

Living task log for the integrated cell. Append new tasks; check items
off (`- [x]`) as they complete. Style follows the sibling projects'
`ToDo.md` convention (append-only history).

## 2026-06-12 | Integration bring-up

### Done
- [x] Combine the three drivers (C.M. / A.P. / P.S.) into
      `pipette_liquid_handler.py` with a single async facade.
- [x] `requirements.txt` + `README.md`.
- [x] Robust port auto-detect by VID:PID (pipette 24bc:2202,
      balance 24bc:0010).
- [x] Real-hardware comms verified (no motion); homing + move-to-T.B.
      verified live; `NOT_ALLOWED` diagnosed as pipette-on-menu.

## 2026-06-12 | Refinement round 1

### 1. Fix C.M. motion model (workflow.md correction)
- [x] Replace the "Z then X, X last" rule with: X traverse at a safe
      travel height -> Z descend to the station -> action -> Z ascend,
      then the next X traverse. (`traverse_to` + `retract`.)
- [x] Add a travel/safe Z height to the configuration (`travel_z_mm`).
- [x] Rework `setup` / `reload_tip` / `dispense_and_weigh` /
      `throw_tip` / `transfer_pass` to the traverse+retract pattern.
- [x] Confirm weigh timing: read **after** the retract (tip clear).
- [x] workflow.md motion rule corrected (user-edited; typo fixed).
- [x] dry-run updated + re-verified ALL PASS for the new model.

### 2. Conform files to the project CLAUDE.md
- [x] Write `PipetteLiquidHandler/CLAUDE.md` (lightweight, TODO.md-
      centric; MIT Python style: 80-col, docstrings, English).
- [x] Add `ruff.toml` (line-length 80, E/F/W/I/N) mirroring siblings.
- [x] `ruff check` + `ruff format` clean on
      `pipette_liquid_handler.py`; fixed broken `_sample_layout`
      import in `bringup_comms.py` (renamed to `layout`).

### 3. Operator configuration block
- [x] Move coordinates into an OPERATOR CONFIGURATION block at the top
      of `pipette_liquid_handler.py` for the user to fill in.

### Awaiting user
- [ ] Measure and enter the real coordinates (T.B./T.S./B1/B2/B31/B32,
      `travel_z_mm`, `tip_interval_mm`, `tip_seat_z_mm`) in the OPERATOR
      CONFIGURATION block, then re-run on hardware.
