# PipetteLiquidHandler

Unified controller that drives three instruments as one gravimetric
liquid-handling cell, sequenced to [`workflow.md`](workflow.md):

- **C.M.** — Cartesian Module (XZ stage, MKS SERVO57D over USB2CAN)
- **A.P.** — Automated Pipette (Sartorius Picus 2, USB-CDC or BLE)
- **P.S.** — Precision Scaler (Sartorius Entris-II balance, SBI serial)

The single file [`pipette_liquid_handler.py`](pipette_liquid_handler.py)
imports the three device drivers from their sibling project trees via
`sys.path` (no packaging step), so **keep this folder next to**
`AutomatedPipette/`, `PrecisionScaleController/`, and
`ESP32S3BOX3MotorController/` — it locates them by relative path.

## Requirements

- **Python 3.11+** (the code uses `typing.Self`).
- **Run as root** — startup calls `prepare_usb_nodes()` / `release_ftdi_sio()`,
  which `os.mknod` device nodes and unbind `ftdi_sio` (root-only).
- System `libusb` present (pyftdi drives the USB2CAN adapters over it).

## Setup (new conda env)

```bash
# 1. Create and activate a fresh env (3.11 or 3.12)
conda create -n plh python=3.11 -y
conda activate plh

# 2. Install dependencies
cd /workspace/PipetteLiquidHandler
pip install -r requirements.txt

# 3. Sanity-check the imports resolve (no hardware touched)
python -c "import serial, pyftdi, bleak; import pipette_liquid_handler; print('ok')"
```

## Hardware preconditions

- **Pipette** in a **Pipetting mode** (screen shows `1000uL` with soft
  keys `MENU / EDIT / ADV`), no blocking dialog. On the mode-selection
  menu, `ENABLE_MOTOR_CONTROL` returns `NOT_ALLOWED`.
- **Balance** front panel set to `COM.OUTP = AUTO W/` and
  `STAB.RNG = V.FAST`, or the post-dispense weighing times out.
- **Stage**: three USB2CAN adapters connected; the X adapter's FTDI
  serial matches `default_x_serial` (`NTAM63XD`) in the module. List
  serials with:
  ```bash
  python -c "from pyftdi.ftdi import Ftdi; print([u.sn for u,_ in Ftdi.list_devices()])"
  ```
- **Ports auto-detect** by VID:PID — pipette `24bc:2202`, balance
  `24bc:0010`. No port config needed. Make sure no other process (an
  old run, `idf.py monitor`, a balance reader) is holding `ttyACM*`.

## Run

```bash
# Communications check only — opens all three devices, NO motion.
python claude_test/bringup_comms.py

# Full workflow.md procedure — homes, then runs both passes with
# real motion + pipetting + weighing.
python pipette_liquid_handler.py
```

## Configuration

Edit the top of [`pipette_liquid_handler.py`](pipette_liquid_handler.py):

- `_sample_layout` (`CellLayout`) — station coordinates as `Point(x_mm,
  z_mm)` absolute from home, plus `tip_interval_mm` and `tip_seat_z_mm`.
  **Currently placeholder** (Z uniformly 100); set real measured values
  before relying on the dispense results.
- `default_x_serial` — FTDI serial of the X-axis adapter.
- `default_move_speed_pct` / `default_pipette_speed` — motion and
  pipetting speeds.

## Layout / debug scripts

- [`claude_test/bringup_comms.py`](claude_test/bringup_comms.py) —
  real-hardware comms check, no motion.
- [`claude_test/dryrun_workflow.py`](claude_test/dryrun_workflow.py) —
  mocked dry-run that verifies the full call ordering.
