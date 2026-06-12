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
- [x] User entered measured coordinates in the OPERATOR CONFIGURATION
      block.
- [ ] Re-run the full workflow on hardware with the real coordinates.

## 2026-06-12 | Refinement round 2

- [x] Slow tip seating: only the tip-insertion Z moves (descent onto the
      tip + the seat press) run at `tip_seat_speed_pct = 3`; the X
      traverse stays at normal speed. Added a `speed_pct` override to
      `move_z` / `move_x`.
- [x] Reinterpret `tip_seat_z_mm` as an **extra press past the approach
      depth** (seat target = `tip_storage.z` + `tip_seat_z_mm`), matching
      the entered values (approach 255, press +10).
- [x] dry-run updated to record per-move speed and assert the 6 slow Z
      moves (3% during seating, no slow X); ALL PASS.
- [x] `requirements-dev.txt` for ruff (runtime deps unchanged).
- [x] Reverted: removed `requirements-dev.txt` — the sibling projects do
      not pin ruff; it stays an ad-hoc dev tool (`ruff.toml` + CLAUDE.md
      cover usage).
- [x] `claude_test/run_z_minus20.py` — full run with every Z shifted by
      -20 mm (floored at 0), derived from the real layout.

## 2026-06-12 | Refinement round 3

- [x] Median weighing: each target weight is the median of
      `weigh_sample_count = 5` stable reads (raw `read_stable_weight`,
      not the jitter-dedup stream, which would drop the repeats);
      `flush_pending_reads()` first drops stale pre-dispense lines.
      `measure_weight()` returns the median + the raw samples.
- [x] CSV weigh log: `_log_weigh` appends each sample and the final
      median per measurement (timestamp, tag, target, volume_ul, kind,
      index, value, unit). `csv_path` ctor arg, else a timestamped
      `weigh_<...>.csv`. `DispenseResult` now carries `samples`.
- [x] dry-run updated (flush + 5 reads + median + CSV asserts); ALL PASS.

## 2026-06-12 | Refinement round 4

- [x] Step narration: each workflow stage logs at INFO (setup, per-pass
      aspirate, per-target move+dispense, blow-out, weigh, tip discard,
      tip load) so the terminal shows what the cell is doing.
- [x] Balance touched only at dispense: removed the startup
      `get_balance_model()` query from `_demo` and `run_z_minus20.py`
      (connection is still validated by opening the port in `connect`).
      Weighing already occurred only in `dispense_and_weigh`.

## 2026-06-12 | Refinement round 5

- [x] Tare before each dispense (`tare_balance`, tip clear) so the
      weighing is the net mass added to that vial; per-vial running
      total accumulated (`_vial_totals`) and logged + written to a new
      `vial_total` CSV column; per-vial total logged at run end.
- [x] Dispense volumes are configurable in the OPERATOR CONFIGURATION
      block (`blue_b31_ul` ... `brown_b32_ul`); validated against the
      pipette's `min_volume_ul`..`max_volume_ul` (50..1000) and each
      pass total checked against the max.
- [x] dry-run updated (tare mock + tare/dispense pairing + vial_total
      accumulation asserts); ALL PASS.

## 2026-06-12 | Refinement round 6

- [x] Configurable starting tip: `tip_start_index` (0-based) in the
      OPERATOR CONFIGURATION block lets a run skip already-consumed
      tips (e.g. 2 = start at the 3rd tip). `tip_count = 8` bounds the
      rack; `reload_tip` raises if a slot exceeds it and `run()`
      validates the two tips fit from the start index. Rack holds 8
      tips, a run consumes 2 -> 4 runs per rack.
- [x] dry-run pins `tip_start_index=0` for determinism; ALL PASS.
