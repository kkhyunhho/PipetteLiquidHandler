"""Dry-run the full PipetteLiquidHandler workflow with mocked devices.

This bench has no pipette and only two FTDI adapters, so the real
hardware path can't run here. Instead we inject recording stubs for the
three drivers (mks_motor / picus2 / entris_ii) into sys.modules *before*
importing the handler, assign arbitrary coordinates, run run(), and print
the exact action trace to verify the "raise, traverse, descend" model:
  - each traverse = move_X then move_Z descend (X first, Z last to act)
  - each station action is followed by a retract (move_Z -> travel)
  - the target weight is read only after that retract (tip clear)
  - blow-out only on each pass's final dispense, before its retract+weigh
  - tip index advances setup(0) -> pass1(1) -> pass2(2)
"""

import asyncio
import os
import sys
import types
from collections import namedtuple

TRAVEL_Z = 0
ACTION_Z = 40  # source / target / trash descend depth in this dry-run

TRACE = []


def log(msg):
    TRACE.append(msg)


# ── Fake mks_motor ────────────────────────────────────────────────────
fake_mks = types.ModuleType("mks_motor")


class FakeMotor:
    def __init__(self, name):
        self.name = name

    def setup(self):
        log(f"CM.setup({self.name})")
        return True

    def move_to(self, mm, speed_pct=20, accel_pct=10):
        log(f"CM.move_X -> {mm} @ {speed_pct}% acc={accel_pct}")

    def close(self):
        log(f"CM.close({self.name})")


class FakeMKSMotor:
    @classmethod
    def open_xz(cls, serial_x):
        log(f"CM.open_xz(serial_x={serial_x})")
        return FakeMotor("Z_A"), FakeMotor("Z_B"), FakeMotor("X")

    @staticmethod
    def home_xz(z_motors, x_motor, dz, dx):
        log(f"CM.home_xz(dz=0x{dz:02X}, dx=0x{dx:02X})")

    @staticmethod
    def move_sync(motors, moves):
        z, speed, accel = moves[0]
        log(f"CM.move_Z -> {z} @ {speed}% acc={accel}")


fake_mks.MKSMotor = FakeMKSMotor
fake_mks.prepare_usb_nodes = lambda: log("CM.prepare_usb_nodes()")
fake_mks.release_ftdi_sio = lambda: log("CM.release_ftdi_sio()")
sys.modules["mks_motor"] = fake_mks


# ── Fake picus2 ───────────────────────────────────────────────────────
fake_picus2 = types.ModuleType("picus2")


class CommandError(Exception):
    pass


class FakePicus2Client:
    def __init__(self, where):
        self.where = where

    @classmethod
    def over_serial(cls, port, **kw):
        return cls(f"serial:{port}")

    @classmethod
    def over_ble(cls, name, **kw):
        return cls(f"ble:{name}")

    @property
    def is_connected(self):
        return True

    async def connect(self):
        log(f"AP.connect({self.where})")

    async def disconnect(self):
        log("AP.disconnect()")

    async def enable_motor_control(self):
        log("AP.enable_motor_control() [motor ON + eject leftover tip]")

    async def disable_motor_control(self):
        log("AP.disable_motor_control() [motor OFF]")

    async def aspirate(self, volume_ul, speed):
        log(f"AP.aspirate({volume_ul} uL, speed={speed})")

    async def dispense(self, volume_ul, speed):
        log(f"AP.dispense({volume_ul} uL, speed={speed})")

    async def blow_out(self, *, speed, go_home=True, delay_ms=3000):
        log(f"AP.blow_out(speed={speed}, go_home={go_home})")

    async def eject_tip(self):
        log("AP.eject_tip()")


fake_picus2.Picus2Client = FakePicus2Client
fake_picus2.CommandError = CommandError
sys.modules["picus2"] = fake_picus2


# ── Fake entris_ii ────────────────────────────────────────────────────
fake_entris = types.ModuleType("entris_ii")
WeightReading = namedtuple("WeightReading", ["value", "unit", "raw"])


class FakeScale:
    # Simulate liquid accumulating on the pan: each read returns a
    # slightly larger mass so the weighing steps show visible change.
    _pan_mass = [0.0]

    def __init__(self, port):
        self.port = port

    @classmethod
    def find_port(cls):
        return "/dev/fake-balance"

    def open(self):
        log("PS.open()")

    def close(self):
        log("PS.close()")

    def get_model_number(self):
        return "FAKE-ENTRIS-II"

    def flush_pending_reads(self):
        log("PS.flush_pending_reads()")

    def read_stable_weight(self, timeout=30.0):
        self._pan_mass[0] += 0.1234
        value = round(self._pan_mass[0], 4)
        log(f"PS.read_stable_weight() -> {value} g")
        return WeightReading(value, "g", f"{value} g")

    def calibrate_internal_very_unstable(self, **kw):
        log("PS.calibrate()")
        return WeightReading(0.0, "g", "0.0 g")


fake_entris.PrecisionScaleController = FakeScale
fake_entris.WeightReading = WeightReading
sys.modules["entris_ii"] = fake_entris


# ── Import the handler (now resolves to the fakes above) ──────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipette_liquid_handler import CellLayout, PipetteLiquidHandler, Point

# Arbitrary but distinct coordinates. Station Z = ACTION_Z (descend),
# traversal at TRAVEL_Z, tip seat deeper than the rack approach height.
layout = CellLayout(
    travel_z_mm=TRAVEL_Z,
    trash_bin=Point(x_mm=10, z_mm=ACTION_Z),
    tip_storage=Point(x_mm=100, z_mm=50),
    tip_interval_mm=9,
    tip_seat_z_mm=70,
    source_blue=Point(x_mm=150, z_mm=ACTION_Z),    # B1
    source_brown=Point(x_mm=200, z_mm=ACTION_Z),   # B2
    target_1=Point(x_mm=250, z_mm=ACTION_Z),       # B31 (on P.S.)
    target_2=Point(x_mm=300, z_mm=ACTION_Z),       # B32 (on P.S.)
)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Explicit fake ports so the mocked run never touches real hardware
    # (the module's VID:PID auto-detect would otherwise scan real ports).
    csv_path = "/tmp/dryrun_weigh.csv"
    async with PipetteLiquidHandler(
        layout,
        pipette_port="/dev/fake-pipette",
        scale_port="/dev/fake-balance",
        csv_path=csv_path,
    ) as cell:
        model = await cell.get_balance_model()
        log(f"PS.get_model_number() -> {model}")
        results = await cell.run()

    print("\n===== ACTION TRACE =====")
    for i, line in enumerate(TRACE, 1):
        print(f"{i:3d}. {line}")

    # ── Automated checks ──────────────────────────────────────────────
    print("\n===== CHECKS =====")
    ok = True

    # 1. Each X move is immediately followed by a Z move (traverse =
    #    move_X then move_Z descend).
    for i, line in enumerate(TRACE):
        if line.startswith("CM.move_X"):
            nxt = TRACE[i + 1] if i + 1 < len(TRACE) else ""
            if not nxt.startswith("CM.move_Z"):
                print(f"FAIL traverse: line {i + 1} {line!r} not followed "
                      f"by move_Z (next={nxt!r})")
                ok = False
    if ok:
        print("PASS: every X move is followed by a Z move (X then Z)")

    retract = f"CM.move_Z -> {TRAVEL_Z} @"  # prefix of a retract move

    # 2. Each measurement starts with a flush that is immediately
    #    preceded by a retract to travel Z (tip raised clear first).
    flushes = [i for i, m in enumerate(TRACE)
               if m == "PS.flush_pending_reads()"]
    if flushes and all(TRACE[i - 1].startswith(retract) for i in flushes):
        print("PASS: every measurement retracts (tip clear) then flushes")
    else:
        print(f"FAIL: a measurement not preceded by retract ({flushes})")
        ok = False

    # 3. blow-out count == 2 (one per pass), each before retract+flush.
    blow = [i for i, m in enumerate(TRACE) if m.startswith("AP.blow_out")]
    if len(blow) == 2 and all(
        TRACE[i + 1].startswith(retract)
        and TRACE[i + 2] == "PS.flush_pending_reads()"
        for i in blow
    ):
        print("PASS: 2 blow-outs, each before its retract + measurement")
    else:
        print(f"FAIL: blow-out placement wrong (indices={blow})")
        ok = False

    # 4. Slow tip-seating: every "@ 3%" move is the final seat press Z
    #    move only (the approach descent runs at normal speed), 1 per
    #    reload x 2 reloads = 2 (setup + blue pass; the final brown pass
    #    does not reload), and no X traverse runs slow.
    slow = [m for m in TRACE if "@ 3%" in m]
    if (len(slow) == 2
            and all(m.startswith("CM.move_Z") for m in slow)
            and not any(m.startswith("CM.move_X") and "@ 3%" in m
                        for m in TRACE)):
        print("PASS: only the seat press is slow (2 slow Z moves, no "
              "slow X/approach)")
    else:
        print(f"FAIL: slow-speed moves unexpected ({slow})")
        ok = False

    # 4b. Every stage move carries the configured 10% accel ramp.
    moves = [m for m in TRACE if m.startswith("CM.move_")]
    if moves and all("acc=10" in m for m in moves):
        print(f"PASS: all {len(moves)} moves use accel 10%")
    else:
        bad = [m for m in moves if "acc=10" not in m]
        print(f"FAIL: moves without acc=10: {bad}")
        ok = False

    # 5. Each result is the median of exactly 5 collected samples.
    import statistics
    flat = [it for items in results.values() for it in items]
    if flat and all(
        len(it.samples) == 5
        and it.reading.value
        == statistics.median(s.value for s in it.samples)
        for it in flat
    ):
        print("PASS: each result is the median of 5 samples")
    else:
        print("FAIL: median / sample-count mismatch")
        ok = False

    # 6. CSV written: header + (5 samples + 1 final) x 4 measurements.
    import csv as _csv
    with open(csv_path) as f:
        rows = list(_csv.reader(f))
    expected = 1 + 4 * (5 + 1)
    finals = [r for r in rows[1:] if r[4] == "final"]
    if len(rows) == expected and rows[0][0] == "timestamp" and len(finals) == 4:
        print(f"PASS: CSV has {len(rows)} rows, {len(finals)} final values")
    else:
        print(f"FAIL: CSV rows={len(rows)} (expected {expected})")
        ok = False

    # Info.
    for liquid, items in results.items():
        for it in items:
            print(f"INFO: {liquid} {it.label} {it.volume_ul}uL -> "
                  f"median {it.reading.value} {it.reading.unit} "
                  f"(n={len(it.samples)})")

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES ABOVE")


if __name__ == "__main__":
    import logging
    asyncio.run(main())
