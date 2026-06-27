# CLAUDE.md

This file guides Claude Code when working in the **PipetteLiquidHandler**
project. For shared conventions — code style, the `elec` env, terminology
(**Level** = control-code depth; **Phase** = SDL hardware stage;
composition = device → **cell** → Phase-system), and task/commit rules —
see **SDLClaude** (`kkhyunhho/SDLClaude`), the single source of truth.

This folder is a **composition cell**: it drives three instruments as one
gravimetric liquid-handling cell. Where this file is silent, SDLClaude
governs.

## Project

PipetteLiquidHandler is the **integration layer** that drives three
instruments as one gravimetric liquid-handling cell, sequenced to
[`workflow.md`](workflow.md):

| Label | Instrument | Driver (source project) | Transport |
|-------|------------|-------------------------|-----------|
| C.M.  | Cartesian Module (XZ stage, MKS SERVO57D ×3) | `mks_motor` (ESP32S3BOX3MotorController) | pyftdi / CAN |
| A.P.  | Automated Pipette (Sartorius Picus 2) | `picus2` (AutomatedPipette) | asyncio, USB-CDC / BLE |
| P.S.  | Precision Scaler (Sartorius Entris-II) | `entris_ii` (PrecisionScaleController) | pyserial / SBI |

The whole controller is one file,
[`pipette_liquid_handler.py`](pipette_liquid_handler.py). The three drivers
(`mks_motor`, `picus2`, `entris_ii`) are `pip install -e`'d into the shared
`elec` env, so it imports them directly — no `sys.path` bootstrap. (The
motor driver is the full ESP32 `mks_motor`, not the MKSServo standalone.)

The pipette is the only async driver, so the facade is async; the
blocking balance/motor calls run in worker threads via
`asyncio.to_thread`.

## Environment

| Item | Detail |
|------|--------|
| Runtime | Docker container (`--privileged`), Ubuntu 24.04 |
| Python | **3.12** (uses `typing.Self`); the shared conda env `elec` |
| Run as | **root** — `prepare_usb_nodes()` / `release_ftdi_sio()` use `os.mknod` and write `/sys` |
| Deps | the three drivers (from `elec`) + `bleak`/`pyftdi`/`pyserial` they pull in; see [`requirements.txt`](requirements.txt) |

Drivers are installed into `elec` (`pip install -e`), so no `sys.path`
bootstrap and no folder-position dependency. The repo has a GitHub remote
(`kkhyunhho/PipetteLiquidHandler`).

## File layout

| Path | Purpose |
|------|---------|
| [`pipette_liquid_handler.py`](pipette_liquid_handler.py) | The combined controller. Coordinates live in the **OPERATOR CONFIGURATION** block at the top. |
| [`cell_types.py`](cell_types.py) | Domain value types (`Point`, `CellLayout`, `DispenseResult`), split out of the controller. |
| [`workflow.md`](workflow.md) | The cell's operating procedure — the source of truth for the sequence. |
| [`TODO.md`](TODO.md) | Living, append-only task log (see Task management). |
| [`requirements.txt`](requirements.txt) | Python deps for a fresh env. |
| [`README.md`](README.md) | Setup + run instructions. |
| [`claude_test/`](claude_test/) | Debug/diagnostic scripts (see Debug files). |

## Commands

```bash
python claude_test/bringup_comms.py   # real-hardware comms check, NO motion
python pipette_liquid_handler.py      # full workflow.md run (real motion)
python claude_test/dryrun_workflow.py # mocked dry-run, verifies ordering
ruff check pipette_liquid_handler.py  # lint (80-col)
ruff format --check pipette_liquid_handler.py
```

## Code conventions (MIT / Google Python style)

- **80-column limit**, 4-space indent, one statement per line.
- `snake_case` for functions/variables/constants, `CamelCase` for
  classes, `lower_case` module names.
- Variables/classes are nouns; functions/methods are verbs.
- **Google-style docstrings** (PEP 257) on every public function and
  class, with `Args:` / `Returns:` / `Raises:` where applicable. State
  *what* and *why*, not *how*.
- Comment only for context or non-obvious choices; never restate code.
- **English only** in code, comments, docstrings, and docs.
- **No magic numbers** in the module — name them (the OPERATOR
  CONFIGURATION block and module constants exist for this).
- Run **Ruff** (`ruff check` + `ruff format --check`) before considering
  a change done.

## Debug file management

Debug, exploratory, and one-off scripts go in
[`claude_test/`](claude_test/), never beside the production module. Add a
row to [`claude_test/README.md`](claude_test/README.md) describing each
script's purpose and what was learned. `claude_test/` scripts are exempt
from the 80-column limit and mandatory docstrings; anything promoted to
production must conform fully.

## Task management (lightweight, TODO.md-centric)

Lightweight, even though the repo has a GitHub remote — no mandatory
issue/branch/PR ceremony for this cell:

1. Keep [`TODO.md`](TODO.md) as a **living, append-only** log. Add a
   dated section per work round; never rewrite or delete past entries.
2. Check items off (`- [x]`) as they complete; append a one-line result.
3. For an ambiguous request, confirm **target / method / purpose** before
   starting.
4. Commits use Conventional Commits and go to `main`.

## Research before coding

Verify a driver's real interface before calling it: read the source in
`AutomatedPipette/src/picus2/`,
`PrecisionScaleController/.../src/entris_ii/`, and
`ESP32S3BOX3MotorController/mks_motor.py`, and check each project's
`README.md` / `claude_test/README.md` / `LearnedPatterns.md` for prior
findings. Trust the source over memory.

## Hardware gotchas (learned on this bench)

- **Run as root**; in Docker, `/dev` is a private tmpfs, so the stage
  bring-up rebuilds device nodes and detaches `ftdi_sio` at connect.
- **Pipette must be in a Pipetting mode**, not the mode-selection menu,
  or `ENABLE_MOTOR_CONTROL` returns `NOT_ALLOWED`. `enable_motor_control`
  ejects a mounted tip as a reset, so `setup()` does it over the trash.
- **Balance must be `COM.OUTP = AUTO W/` + `STAB.RNG = V.FAST`** (front
  panel) or stable-weight reads time out.
- **Pipette and balance share the Sartorius VID** (`0x24bc`); ports are
  auto-detected by full VID:PID (pipette `:2202`, balance `:0010`).
  Matching on VID alone grabs the wrong device.
- **Motion model**: X traverses at `travel_z_mm` (Z raised clear), then
  Z descends to the action; after the action Z ascends before the next X
  traverse. Target weights are read only after the retract (tip clear).
- **Z axis uses `coord_invert`** in `mks_motor`; pass non-negative mm.
  The MKS firmware drops the first motion command after a limit stop —
  `mks_motor` already absorbs this. Do not bypass it.
