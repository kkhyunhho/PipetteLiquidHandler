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

## Demonstration videos

The four gravimetric validation runs, each combining a blue and a brown
dye solution into two vials in complementary volume ratios summing to
900 µL. Click a thumbnail to watch on YouTube.

| Run 1 — (300, 600) µL | Run 2 — (100, 800) µL |
|:---:|:---:|
| [![Run 1](https://img.youtube.com/vi/2ytvMtxCstg/0.jpg)](https://youtu.be/2ytvMtxCstg) | [![Run 2](https://img.youtube.com/vi/2bcvVxeyy5A/0.jpg)](https://youtu.be/2bcvVxeyy5A) |
| **Run 3 — (250, 650) µL** | **Run 4 — (350, 550) µL** |
| [![Run 3](https://img.youtube.com/vi/83AHQyNIJ6E/0.jpg)](https://youtu.be/83AHQyNIJ6E) | [![Run 4](https://img.youtube.com/vi/EAFA03EVw3M/0.jpg)](https://youtu.be/EAFA03EVw3M) |

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

Edit the **OPERATOR CONFIGURATION** block at the top of
[`pipette_liquid_handler.py`](pipette_liquid_handler.py):

- Station coordinates `*_mm = (X_mm, Z_mm)` — absolute from the homed
  origin. X is the column; Z is the descend depth for the action there.
- `travel_z_mm` — safe clearance height for X traversal (Z raised).
- `tip_interval_mm` — X spacing between tips; `tip_seat_z_mm` — extra Z
  press past the approach to seat a tip.
- `tip_count` (rack size, default 8) and `tip_start_index` (0-based
  first tip; set to 2 to begin at the 3rd tip after consuming two). A
  run uses 2 tips, so an 8-tip rack does 4 runs.
- `x_axis_serial`, `move_speed_pct`, `move_accel_pct` (smoothing ramp),
  `tip_seat_speed_pct` (slow tip seating), `pipette_speed`,
  `weigh_sample_count`.
- Dispense volumes `blue_b31_ul` ... `brown_b32_ul` — each within the
  pipette's `min_volume_ul`..`max_volume_ul` (50..1000 uL); each pass
  aspirates the sum of its two volumes (also capped at the max).

## Weigh log (CSV)

The balance is **tared before each dispense** (tip clear), so each
weighing is the **net mass delivered to that vial**, taken as the
**median of `weigh_sample_count` (5) stable reads** — robust against an
outlier. Every sample, the net median, and the running per-vial total
are written to a CSV (`csv_path` constructor arg, else a timestamped
`weigh_<...>.csv`): columns `timestamp, tag, target, volume_ul, kind,
index, value, unit, vial_total`.

## Layout / debug scripts

- [`claude_test/bringup_comms.py`](claude_test/bringup_comms.py) —
  real-hardware comms check, no motion.
- [`claude_test/run_z_minus20.py`](claude_test/run_z_minus20.py) —
  full run with every Z shifted by -20 mm (derived from the real layout).
- [`claude_test/dryrun_workflow.py`](claude_test/dryrun_workflow.py) —
  mocked dry-run that verifies the full call ordering.
