"""Unified controller for the gravimetric liquid-handling cell.

``PipetteLiquidHandler`` drives three independent instruments through a
single async facade, sequenced to the cell's operating procedure in
``workflow.md``:

* **C.M.** — Cartesian Module: paired Z + single X MKS SERVO57D
  closed-loop steppers, via :class:`mks_motor.MKSMotor` (blocking
  pyftdi/CAN, one USB2CAN adapter per motor). Source:
  ``ESP32S3BOX3MotorController``.
* **A.P.** — Automated Pipette: Sartorius Picus 2, via
  :class:`picus2.Picus2Client` (asyncio, BLE or USB-CDC). Source:
  ``AutomatedPipette``.
* **P.S.** — Precision Scaler: Sartorius Entris-II balance, via
  :class:`entris_ii.PrecisionScaleController` (blocking pyserial, SBI).
  Source: ``PrecisionScaleController``.

The pipette is the only async device, so it sets the interface: every
public method is a coroutine. The balance and the motors are blocking,
so their calls are dispatched to worker threads with
:func:`asyncio.to_thread`, keeping the event loop responsive while a
move or a stable-weight read is in flight.

The stage motion rule from ``workflow.md`` is "raise, traverse, descend":

1. C.M. motion is **separated into single-axis X and Z moves** — never a
   simultaneous diagonal move.
2. A station is reached by traversing in **X at the safe travel height**
   (Z raised clear of labware), then **descending Z** to the action
   depth. After the action, Z **ascends** back to the travel height
   before the next X traverse. :meth:`traverse_to` and :meth:`retract`
   implement this; every station visit is
   ``traverse_to -> action -> retract``, leaving Z at the travel height.

Coordinates, the travel height, and tip geometry are bench-specific and
live in the **OPERATOR CONFIGURATION** block below; measure and fill
them in before running.

Example:
    async def run() -> None:
        async with PipetteLiquidHandler(layout) as cell:
            await cell.run()

    asyncio.run(run())

Hardware preconditions (see the source projects' CLAUDE.md files):

* The Picus 2 must be in a Pipetting mode (not the mode-selection menu)
  or ``ENABLE_MOTOR_CONTROL`` returns ``NOT_ALLOWED``. Motor control is
  enabled during :meth:`setup` over the trash bin, so the leftover-tip
  eject that authorization triggers lands in the bin.
* The Entris-II must be set to SBI / ``COM.OUTP = AUTO W/`` /
  ``STAB.RNG = V.FAST`` on its front panel for stable-weight reads.
* The three USB2CAN adapters must be wired one-per-motor; only the X
  adapter is named by FTDI serial, the two Z adapters are auto-assigned.
* Inside Docker, ``prepare_usb_nodes`` / ``release_ftdi_sio`` run at
  connect time to rebuild ``/dev`` nodes and detach ``ftdi_sio``.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import statistics
import threading
from datetime import datetime
from types import TracebackType
from typing import Self

# The three instrument drivers (picus2, entris_ii, mks_motor) are
# pip install -e'd into the shared `elec` env, so they import directly —
# no sys.path bootstrap.
from entris_ii import PrecisionScaleController, WeightReading
from mks_motor import MKSMotor, prepare_usb_nodes, release_ftdi_sio
from picus2 import CommandError, Picus2Client

from cell_types import CellLayout, DispenseResult, Point

logger = logging.getLogger(__name__)

# Sartorius USB identifiers. The Picus 2 pipette and the Entris-II
# balance share the Sartorius vendor ID and both enumerate as CDC-ACM,
# so ports are auto-detected by the full VID:PID pair — matching on VID
# alone would grab whichever device enumerated first. Not user-tuned.
sartorius_vid = 0x24BC
picus_pid = 0x2202
entris_pid = 0x0010

# ══════════════════════════════════════════════════════════════════════
#  OPERATOR CONFIGURATION — measure and fill in for the physical bench
# ══════════════════════════════════════════════════════════════════════
# Station coordinates are absolute millimeters from the homed origin,
# each given as ``(X_mm, Z_mm)``:
#   * X_mm — horizontal position of the station.
#   * Z_mm — descend depth where the action happens at that station
#            (submersion depth for a source, dispense height over a
#            target, tip-approach height over the rack).
# The tip traverses in X at ``travel_z_mm`` (a safe clearance height,
# Z raised) between stations, then descends to each station's Z. Choose
# every Z so the X traverse at ``travel_z_mm`` clears all labware.

travel_z_mm = 0  # safe clearance height for X traversal

trash_bin_mm = (0, 175)  # T.B. — used-tip drop
tip_storage_mm = (55, 255)  # T.S. — first tip (slot 0) approach
source_blue_mm = (180, 70)  # B1 — blue liquid
source_brown_mm = (325, 70)  # B2 — brown liquid
target_1_mm = (235, 50)  # B31 — first target (on the balance pan)
target_2_mm = (275, 50)  # B32 — second target (on the balance pan)

tip_interval_mm = 9.5  # X spacing between adjacent tips in the rack
tip_seat_z_mm = 10  # extra Z press past the approach depth to seat a tip
tip_count = 8  # tips available in the rack (a run consumes 2)
tip_start_index = 6  # 0-based first tip slot (0 = leftmost; 2 = 3rd tip)

# Stage / pipette motion knobs (rarely edited).
x_axis_serial = "NTAM63XD"  # FTDI chip serial of the X-axis adapter
move_speed_pct = 10  # stage absolute-move speed, % of max RPM
move_accel_pct = 10  # accel ramp on every move (0 = abrupt), % of max
tip_seat_speed_pct = 3  # slow Z speed while seating a tip, % of max RPM
pipette_speed = 6  # default pipette motor speed, 1..9
weigh_sample_count = 5  # stable reads per weighing; final value = median

# Dispense volumes (uL) per pass and target. The balance is tared before
# each dispense, so the weighing reports the net mass added to that vial.
# The pipette handles min_volume_ul..max_volume_ul per operation, and
# each pass aspirates the sum of its two volumes (so each pass total must
# also stay within max_volume_ul).
min_volume_ul = 50  # pipette per-operation minimum (do not lower)
max_volume_ul = 1000  # pipette per-operation maximum (do not raise)
blue_b31_ul = 350  # blue liquid -> B31
blue_b32_ul = 550  # blue liquid -> B32
brown_b31_ul = 550  # brown liquid -> B31
brown_b32_ul = 350  # brown liquid -> B32

home_dir_z = 0x00  # Z homing direction byte
home_dir_x = 0x01  # X homing direction byte
# ══════════════════════════════════════════════════════════════════════


def find_usb_serial_port(vid: int, pid: int) -> str | None:
    """Return the serial device path matching ``vid``:``pid``, or None.

    Scans the available serial ports for an exact VID:PID match. Used to
    pin the pipette and the balance apart, since both are Sartorius
    CDC-ACM devices sharing one vendor ID.

    Args:
        vid: USB vendor ID to match.
        pid: USB product ID to match.

    Returns:
        The first matching device path, or None if none is present.
    """
    import serial.tools.list_ports

    for info in serial.tools.list_ports.comports():
        if info.vid == vid and info.pid == pid:
            return info.device
    return None


class PipetteLiquidHandler:
    """Single async facade over the C.M., A.P., and P.S., sequenced to
    ``workflow.md``.

    Construct with a :class:`CellLayout` plus optional per-instrument
    connection settings, then either drive the full procedure with
    :meth:`run` or compose the granular steps (:meth:`traverse_to`,
    :meth:`retract`, :meth:`aspirate`, :meth:`dispense_and_weigh`,
    :meth:`throw_tip`, :meth:`reload_tip`) yourself.

    Use as an async context manager so every device is connected on
    entry and released on exit even if a routine raises::

        async with PipetteLiquidHandler(layout) as cell:
            await cell.run()
    """

    def __init__(
        self,
        layout: CellLayout,
        *,
        pipette_port: str | None = None,
        pipette_ble_name: str | None = None,
        scale_port: str | None = None,
        x_serial: str = x_axis_serial,
        home_dir_z: int = home_dir_z,
        home_dir_x: int = home_dir_x,
        move_speed_pct: int = move_speed_pct,
        move_accel_pct: int = move_accel_pct,
        tip_seat_speed_pct: int = tip_seat_speed_pct,
        pipette_speed: int = pipette_speed,
        weigh_sample_count: int = weigh_sample_count,
        tip_count: int = tip_count,
        tip_start_index: int = tip_start_index,
        csv_path: str | None = None,
    ) -> None:
        """Record the layout and connection settings without opening any
        device.

        Args:
            layout: Bench coordinates and tip geometry.
            pipette_port: USB-CDC serial path for the Picus 2, or None to
                auto-detect by VID:PID. Ignored when ``pipette_ble_name``
                is given.
            pipette_ble_name: Advertised BLE name; selects the BLE
                transport instead of USB serial when set.
            scale_port: Serial path for the Entris-II, or None to
                auto-detect by VID:PID.
            x_serial: FTDI chip serial of the X-axis USB2CAN adapter.
            home_dir_z: Homing direction byte for the paired Z axis.
            home_dir_x: Homing direction byte for the X axis.
            move_speed_pct: Absolute-move speed, percent of max RPM.
            move_accel_pct: Absolute-move acceleration, percent of max.
            tip_seat_speed_pct: Slow Z speed used while seating a tip,
                percent of max RPM.
            pipette_speed: Default pipette motor speed (1..9).
            weigh_sample_count: Stable reads collected per weighing; the
                reported value is their median.
            tip_count: Tips available in the rack; reloading beyond the
                last slot raises.
            tip_start_index: 0-based slot of the first tip to use, so a
                run can skip already-consumed tips (e.g. 2 starts at the
                third tip).
            csv_path: Where to write the weigh log, or None to create a
                timestamped ``weigh_<...>.csv`` on the first weighing.
        """
        self._layout = layout
        self._pipette_port = pipette_port
        self._pipette_ble_name = pipette_ble_name
        self._scale_port = scale_port
        self._x_serial = x_serial
        self._home_dir_z = home_dir_z
        self._home_dir_x = home_dir_x
        self._move_speed_pct = move_speed_pct
        self._move_accel_pct = move_accel_pct
        self._tip_seat_speed_pct = tip_seat_speed_pct
        self._pipette_speed = pipette_speed
        self._weigh_sample_count = weigh_sample_count

        # Weigh-log CSV. Opened lazily on the first weighing so a run
        # that never weighs leaves no file behind.
        self._csv_path = csv_path
        self._csv_file = None
        self._csv_writer = None

        self._pipette: Picus2Client | None = None
        self._scale: PrecisionScaleController | None = None
        self._z_motors: list[MKSMotor] = []
        self._x_motor: MKSMotor | None = None

        # Tip rack bookkeeping. ``_tip_index`` is the next slot to seat,
        # advanced by reload_tip; it starts at ``tip_start_index`` so a
        # run can skip already-consumed tips, and may not reach
        # ``tip_count``.
        self._tip_count = tip_count
        self._tip_start_index = tip_start_index
        self._tip_index = tip_start_index

        # Running net mass delivered per target vial (label -> grams),
        # accumulated across passes since each dispense is tared.
        self._vial_totals: dict[str, float] = {}

        # Serializes blocking motor calls. The MKS adapters are not safe
        # to drive from two coroutines at once, and to_thread runs on a
        # pool, so a lock keeps stage moves strictly sequential.
        self._motor_lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def __aenter__(self) -> Self:
        """Connect every instrument on entering the context."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release every instrument on leaving the context."""
        await self.close()

    @property
    def is_connected(self) -> bool:
        """True once :meth:`connect` has brought up all three devices."""
        return (
            self._pipette is not None
            and self._pipette.is_connected
            and self._scale is not None
            and self._x_motor is not None
        )

    async def connect(self, *, home: bool = True) -> None:
        """Bring up the stage, the balance, and the pipette transport.

        Opens all three devices and, by default, homes the stage and
        raises Z to the travel height so the "raise, traverse, descend"
        invariant holds before the first station visit. The pipette
        transport is connected but its motor is **not** enabled here —
        that happens in :meth:`setup` over the trash bin.

        Args:
            home: Home the stage (and retract to the travel height) after
                opening. Pass ``False`` for a motion-free bring-up that
                only verifies communications with all three instruments.

        Raises:
            DeviceNotFoundError: If the pipette cannot be reached.
            ConnectionError: If a motor adapter does not respond.
            RuntimeError: If a device port cannot be resolved or opened.
        """
        await asyncio.to_thread(self._open_stage)
        await asyncio.to_thread(self._open_scale)
        await self._open_pipette()
        if home:
            await self.home()
            # Establish the travel-height invariant: every later station
            # visit assumes Z starts raised at the travel height.
            await self.retract()
        logger.info(
            "PipetteLiquidHandler connected%s",
            " and homed" if home else " (no homing)",
        )

    async def close(self) -> None:
        """Disconnect every instrument, ignoring individual failures.

        Each device is released independently so a fault in one does not
        strand the others; failures are logged rather than raised.
        """
        if self._pipette is not None:
            try:
                await self._pipette.disable_motor_control()
            except Exception as error:
                logger.warning("pipette motor disable failed: %s", error)
            try:
                await self._pipette.disconnect()
            except Exception as error:
                logger.warning("pipette disconnect failed: %s", error)
            self._pipette = None

        if self._scale is not None:
            try:
                await asyncio.to_thread(self._scale.close)
            except Exception as error:
                logger.warning("scale close failed: %s", error)
            self._scale = None

        for motor in self._z_motors + (
            [self._x_motor] if self._x_motor else []
        ):
            try:
                await asyncio.to_thread(motor.close)
            except Exception as error:
                logger.warning("motor close failed: %s", error)
        self._z_motors = []
        self._x_motor = None

        if self._csv_file is not None:
            try:
                self._csv_file.close()
            except Exception as error:
                logger.warning("csv close failed: %s", error)
            self._csv_file = None
            self._csv_writer = None
        logger.info("PipetteLiquidHandler closed")

    # ── Per-instrument bring-up (blocking; run via to_thread) ─────────

    def _open_stage(self) -> None:
        """Open and configure the XZ motors (blocking)."""
        # Docker's /dev is a private tmpfs; rebuild adapter + CDC nodes
        # and detach ftdi_sio so pyftdi can claim the adapters. No-ops
        # on a non-Docker host that already has stable nodes.
        prepare_usb_nodes()
        release_ftdi_sio()

        za, zb, x = MKSMotor.open_xz(self._x_serial)
        for label, motor in (("Z_A", za), ("Z_B", zb), ("X", x)):
            if not motor.setup():
                logger.warning("%s motor setup reported failure", label)
        self._z_motors = [za, zb]
        self._x_motor = x

    def _open_scale(self) -> None:
        """Open the balance serial port (blocking).

        Raises:
            RuntimeError: If no port is given and no Entris-II is found.
        """
        port = self._scale_port or find_usb_serial_port(
            sartorius_vid, entris_pid
        )
        if port is None:
            raise RuntimeError(
                "no balance port given and no Entris-II (24bc:0010) found"
            )
        scale = PrecisionScaleController(port=port)
        scale.open()
        self._scale = scale

    async def _open_pipette(self) -> None:
        """Connect the pipette transport (motor enabled later in setup).

        Raises:
            RuntimeError: If USB is selected but no port is given and no
                Picus 2 is found.
        """
        if self._pipette_ble_name is not None:
            client = Picus2Client.over_ble(self._pipette_ble_name)
        else:
            port = self._pipette_port or find_usb_serial_port(
                sartorius_vid, picus_pid
            )
            if port is None:
                raise RuntimeError(
                    "no pipette port given and no Picus 2 (24bc:2202) found"
                )
            client = Picus2Client.over_serial(port)
        await client.connect()
        self._pipette = client

    # ── Internal accessors with a clear connected-state error ─────────

    def _require_pipette(self) -> Picus2Client:
        if self._pipette is None:
            raise RuntimeError("pipette not connected; call connect() first")
        return self._pipette

    def _require_scale(self) -> PrecisionScaleController:
        if self._scale is None:
            raise RuntimeError("balance not connected; call connect() first")
        return self._scale

    def _require_x_motor(self) -> MKSMotor:
        if self._x_motor is None:
            raise RuntimeError("stage not connected; call connect() first")
        return self._x_motor

    # ── C.M.: stage motion (X and Z strictly separated) ───────────────

    async def home(self) -> None:
        """Home the paired Z axis and then the X axis.

        Homing also arms the limit switches, so it must precede any
        absolute move after power-up. :meth:`connect` follows it with a
        :meth:`retract` to reach the travel height.
        """
        await asyncio.to_thread(self._home_blocking)

    def _home_blocking(self) -> None:
        with self._motor_lock:
            MKSMotor.home_xz(
                self._z_motors,
                self._require_x_motor(),
                self._home_dir_z,
                self._home_dir_x,
            )

    async def move_z(self, z_mm: int, speed_pct: int | None = None) -> None:
        """Move only the paired Z axis to ``z_mm`` (X held in place).

        Args:
            z_mm: Absolute Z target in millimeters.
            speed_pct: Override the default move speed (percent of max
                RPM); used for the slow tip-seating descent.
        """
        await asyncio.to_thread(self._move_z_blocking, z_mm, speed_pct)

    async def move_x(self, x_mm: int, speed_pct: int | None = None) -> None:
        """Move only the X axis to ``x_mm`` (Z held in place).

        Args:
            x_mm: Absolute X target in millimeters.
            speed_pct: Override the default move speed (percent of max
                RPM).
        """
        await asyncio.to_thread(self._move_x_blocking, x_mm, speed_pct)

    def _move_z_blocking(self, z_mm: int, speed_pct: int | None = None) -> None:
        speed = self._move_speed_pct if speed_pct is None else speed_pct
        with self._motor_lock:
            MKSMotor.move_sync(
                self._z_motors,
                [(z_mm, speed, self._move_accel_pct)],
            )

    def _move_x_blocking(self, x_mm: int, speed_pct: int | None = None) -> None:
        speed = self._move_speed_pct if speed_pct is None else speed_pct
        with self._motor_lock:
            self._require_x_motor().move_to(
                x_mm,
                speed_pct=speed,
                accel_pct=self._move_accel_pct,
            )

    async def traverse_to(self, point: Point) -> None:
        """Traverse in X at travel height, then descend Z to ``point``.

        The standard approach before every action: with Z already raised
        at the travel height (left there by :meth:`retract`, :meth:`home`
        + :meth:`connect`, or a prior visit), move X over the station,
        then descend Z to the action depth. X and Z stay separate moves.

        Args:
            point: Absolute ``(x_mm, z_mm)`` station target.
        """
        await self.move_x(point.x_mm)
        await self.move_z(point.z_mm)

    async def retract(self) -> None:
        """Raise Z to the travel height so the next X traverse is clear."""
        await self.move_z(self._layout.travel_z_mm)

    # ── A.P.: pipette primitives ──────────────────────────────────────

    async def aspirate(self, volume_ul: int, speed: int | None = None) -> None:
        """Aspirate ``volume_ul`` microliters at motor ``speed`` (1..9)."""
        await self._require_pipette().aspirate(
            volume_ul, speed or self._pipette_speed
        )

    async def dispense(self, volume_ul: int, speed: int | None = None) -> None:
        """Dispense ``volume_ul`` microliters at motor ``speed`` (1..9)."""
        await self._require_pipette().dispense(
            volume_ul, speed or self._pipette_speed
        )

    async def blow_out(self, speed: int | None = None) -> None:
        """Blow out residual liquid and return the piston home."""
        await self._require_pipette().blow_out(
            speed=speed or self._pipette_speed
        )

    async def eject_tip(self) -> None:
        """Eject the mounted tip (pipette mechanism)."""
        await self._require_pipette().eject_tip()

    # ── P.S.: balance ─────────────────────────────────────────────────

    async def read_weight(self) -> WeightReading:
        """Return the next auto-pushed stable weight from the balance.

        Requires the balance front panel set to ``COM.OUTP = AUTO W/``;
        see the module docstring.

        Returns:
            The first parseable :class:`WeightReading` observed.

        Raises:
            TimeoutError: If no stable reading arrives in the read window.
        """
        return await asyncio.to_thread(self._require_scale().read_stable_weight)

    async def measure_weight(
        self, count: int | None = None
    ) -> tuple[WeightReading, list[WeightReading]]:
        """Collect several stable reads and reduce them to their median.

        Flushes stale buffered lines, then reads ``count`` consecutive
        stable weights — the BCE224I keeps pushing near-duplicates at the
        0.001 g level under AUTO W/, so repeated reads sample the settled
        value — and takes the median, which rejects an occasional
        outlier reading. The individual samples are returned too, for the
        weigh log.

        Args:
            count: Samples to collect; defaults to ``weigh_sample_count``.

        Returns:
            ``(final, samples)`` where ``final`` is a :class:`WeightReading`
            at the median value and ``samples`` are the raw reads.

        Raises:
            TimeoutError: If a stable reading does not arrive in time.
        """
        n = count or self._weigh_sample_count
        samples = await asyncio.to_thread(self._collect_samples_blocking, n)
        value = statistics.median(s.value for s in samples)
        unit = samples[0].unit if samples else ""
        return WeightReading(value, unit, f"median of {n}"), samples

    def _collect_samples_blocking(self, count: int) -> list[WeightReading]:
        scale = self._require_scale()
        # Drop stale pre-dispense lines so every sample is post-settle.
        scale.flush_pending_reads()
        return [scale.read_stable_weight() for _ in range(count)]

    def _ensure_csv(self) -> None:
        """Open the weigh-log CSV and write its header on first use."""
        if self._csv_writer is not None:
            return
        path = self._csv_path or datetime.now().strftime(
            "weigh_%Y%m%d_%H%M%S.csv"
        )
        self._csv_path = path
        self._csv_file = open(path, "w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(
            [
                "timestamp",
                "tag",
                "target",
                "volume_ul",
                "kind",
                "index",
                "value",
                "unit",
                "vial_total",
            ]
        )
        logger.info("weigh log: %s", path)

    def _log_weigh(
        self,
        tag: str,
        label: str,
        volume_ul: int,
        samples: list[WeightReading],
        final: WeightReading,
        vial_total: float,
    ) -> None:
        """Append a measurement's samples, net median, and vial total.

        ``final`` is the net mass of this dispense (the balance was
        tared first); ``vial_total`` is the running sum delivered to this
        vial across passes.
        """
        self._ensure_csv()
        stamp = datetime.now().isoformat(timespec="seconds")
        for index, sample in enumerate(samples):
            self._csv_writer.writerow(
                [
                    stamp,
                    tag,
                    label,
                    volume_ul,
                    "sample",
                    index,
                    f"{sample.value:.4f}",
                    sample.unit,
                    "",
                ]
            )
        self._csv_writer.writerow(
            [
                stamp,
                tag,
                label,
                volume_ul,
                "final",
                "",
                f"{final.value:.4f}",
                final.unit,
                f"{vial_total:.4f}",
            ]
        )
        self._csv_file.flush()

    async def tare_balance(self) -> None:
        """Zero the balance so the next reading is the net mass added.

        Tared with the tip clear (at the travel height) before a
        dispense, so the post-dispense weighing reports only the mass
        delivered to that vial — independent of anything else on the pan.
        """
        await asyncio.to_thread(self._require_scale().tare)

    async def calibrate_balance(self) -> WeightReading:
        """Run the balance internal calibration (pan must be empty)."""
        return await asyncio.to_thread(
            self._require_scale().calibrate_internal_very_unstable
        )

    async def get_balance_model(self) -> str:
        """Return the balance model number string."""
        return await asyncio.to_thread(self._require_scale().get_model_number)

    # ── Tip management (T.B. / T.S.) ──────────────────────────────────

    async def throw_tip(self) -> None:
        """Traverse to the trash bin, eject the used tip, then retract."""
        logger.info("[tip] discard used tip at trash bin")
        await self.traverse_to(self._layout.trash_bin)
        await self.eject_tip()
        await self.retract()

    async def reload_tip(self) -> None:
        """Seat the next fresh tip from the rack.

        Traverses in X to tip slot ``self._tip_index`` and descends to
        the rack approach height at the usual speed, then presses the
        extra ``tip_seat_z_mm`` deeper at the slow ``tip_seat_speed_pct``
        — only this final seating press is slowed, so the nozzle eases
        the tip on gently. Finally retracts to the travel height with the
        tip attached and advances the tip index.
        """
        slot = self._tip_index
        if slot >= self._tip_count:
            raise RuntimeError(
                f"tip rack exhausted: slot {slot} >= tip_count "
                f"{self._tip_count}"
            )
        logger.info("[tip] load new tip #%d (slow seat press)", slot)
        tip = Point(
            x_mm=self._layout.tip_storage.x_mm
            + slot * self._layout.tip_interval_mm,
            z_mm=self._layout.tip_storage.z_mm,
        )
        # Traverse and descend to the approach height at the usual speed;
        # only the final seat press (the tip_seat_z_mm extra) is slowed.
        await self.move_x(tip.x_mm)
        await self.move_z(tip.z_mm)
        seat_z = tip.z_mm + self._layout.tip_seat_z_mm
        await self.move_z(seat_z, speed_pct=self._tip_seat_speed_pct)
        await self.retract()
        self._tip_index += 1
        logger.info("seated tip #%d", slot)

    # ── Combined workflow steps ───────────────────────────────────────

    async def setup(self) -> None:
        """Run the one-time Setting phase from ``workflow.md``.

        Traverses over the trash bin, enables pipette motor control (its
        leftover-tip eject lands in the bin), retracts, then seats the
        first fresh tip from the rack.

        Raises:
            RuntimeError: If the pipette refuses motor control (e.g. it
                is on the mode-selection menu rather than a Pipetting
                mode).
        """
        logger.info("[setup] trash bin -> enable motor -> load first tip")
        await self.traverse_to(self._layout.trash_bin)
        # Authorizing motor control ejects any mounted tip as a reset;
        # doing it over the bin keeps that tip out of the workspace.
        try:
            await self._require_pipette().enable_motor_control()
        except CommandError as error:
            # NOT_ALLOWED here means the Picus is on the mode-selection
            # menu, not a Pipetting mode (AutomatedPipette claude_test
            # notes). Translate to an actionable operator message.
            raise RuntimeError(
                f"pipette refused motor control ({error}). Put the Picus "
                "2 in a Pipetting mode (not the mode-selection menu) and "
                "clear any on-screen dialog, then retry."
            ) from error
        await self.retract()
        await self.reload_tip()
        logger.info("setup complete; first tip seated")

    async def dispense_and_weigh(
        self,
        target: Point,
        volume_ul: int,
        label: str,
        *,
        tag: str = "",
        blow_out: bool = False,
    ) -> DispenseResult:
        """Tare, dispense into a target, retract, then weigh the net.

        The balance is tared (tip clear) **before** the dispense, so the
        weighing — taken after the tip retracts clear, as the median of
        ``weigh_sample_count`` stable reads — is the net mass delivered to
        this vial. The samples, the net median, and the running per-vial
        total are appended to the weigh-log CSV.

        Args:
            target: Target vial position (on the P.S. pan).
            volume_ul: Volume to dispense, in microliters.
            label: Identifier recorded in the result (e.g. ``"B31"``).
            tag: Pass label for the CSV (e.g. ``"blue"``).
            blow_out: Blow out residual after the dispense, before
                retracting, so the expelled remainder lands on the pan
                and is captured in the reading. Set only on the final
                dispense of a pass — an intermediate blow-out would expel
                liquid still owed to later targets.

        Returns:
            The dispense volume paired with the net median reading and
            its samples.
        """
        logger.info(
            "[%s] %s: tare -> dispense %d uL -> weigh net",
            tag,
            label,
            volume_ul,
        )
        # Zero the balance with the tip clear so the post-dispense
        # reading is only the mass added to this vial.
        await self.tare_balance()
        await self.traverse_to(target)
        await self.dispense(volume_ul)
        if blow_out:
            # Still over the target; expel the clinging remainder so it
            # is weighed, before retracting clear for the reading.
            logger.info("[%s] %s: blow out residual", tag, label)
            await self.blow_out()
        await self.retract()
        final, samples = await self.measure_weight()
        total = self._vial_totals.get(label, 0.0) + final.value
        self._vial_totals[label] = total
        self._log_weigh(tag, label, volume_ul, samples, final, total)
        logger.info(
            "%s/%s: +%.4f %s net (vial total %.4f %s)",
            tag,
            label,
            final.value,
            final.unit,
            total,
            final.unit,
        )
        return DispenseResult(label, volume_ul, final, samples)

    async def transfer_pass(
        self,
        source: Point,
        plan: list[tuple[Point, int, str]],
        tag: str = "",
        reload_after: bool = True,
    ) -> list[DispenseResult]:
        """Run one closed-loop pass: aspirate, dispense+weigh, swap tip.

        Traverses to ``source`` and aspirates the summed plan volume,
        retracts, dispenses each planned aliquot into its target while
        weighing, then discards the used tip and (optionally) seats a
        fresh one.

        Args:
            source: Source vial to aspirate from.
            plan: Ordered ``(target, volume_ul, label)`` aliquots.
            tag: Pass label recorded in the weigh log (e.g. ``"blue"``).
            reload_after: Seat a fresh tip after discarding the used one.
                Set False on the final pass — nothing follows it, so the
                run ends with the tip simply discarded.

        Returns:
            One :class:`DispenseResult` per planned aliquot.
        """
        logger.info("=== pass: %s ===", tag or "(untagged)")
        total_ul = sum(volume for _, volume, _ in plan)
        if total_ul > max_volume_ul:
            raise ValueError(
                f"{tag} pass aspirates {total_ul} uL, over the "
                f"{max_volume_ul} uL pipette maximum"
            )
        logger.info("[%s] aspirate %d uL from source", tag, total_ul)
        await self.traverse_to(source)
        await self.aspirate(total_ul)
        await self.retract()

        results = []
        last = len(plan) - 1
        for index, (target, volume, label) in enumerate(plan):
            # Blow out only on the final aliquot: earlier blow-outs would
            # expel liquid still owed to the remaining targets.
            results.append(
                await self.dispense_and_weigh(
                    target,
                    volume,
                    label,
                    tag=tag,
                    blow_out=(index == last),
                )
            )

        await self.throw_tip()
        if reload_after:
            await self.reload_tip()
        return results

    async def end(self) -> None:
        """Run the Ending phase: turn off the pipette motor."""
        await self._require_pipette().disable_motor_control()
        logger.info("pipette motor disabled")

    async def run(self) -> dict[str, list[DispenseResult]]:
        """Execute the full ``workflow.md`` procedure end to end.

        Setting, then the blue pass (B1 -> ``blue_b31_ul`` into B31,
        ``blue_b32_ul`` into B32), then the brown pass (B2 ->
        ``brown_b31_ul`` into B31, ``brown_b32_ul`` into B32), then
        Ending. The four volumes come from the OPERATOR CONFIGURATION
        block. Each pass aspirates its two volumes' sum once; every
        dispense tares first and weighs the net (tip retracted), blowing
        out residual on the final dispense. The blue pass swaps in a
        fresh tip; the brown (final) pass discards its tip without
        reloading. A per-vial total is logged at the end.

        Returns:
            ``{"blue": [...], "brown": [...]}`` dispense/weigh results.

        Raises:
            ValueError: If a configured volume is outside
                ``[min_volume_ul, max_volume_ul]``, a pass total exceeds
                the pipette maximum, or the run's two tips do not fit in
                the rack from ``tip_start_index``.
        """
        for volume, ctx in (
            (blue_b31_ul, "blue->B31"),
            (blue_b32_ul, "blue->B32"),
            (brown_b31_ul, "brown->B31"),
            (brown_b32_ul, "brown->B32"),
        ):
            if not min_volume_ul <= volume <= max_volume_ul:
                raise ValueError(
                    f"{ctx} volume {volume} uL out of range "
                    f"[{min_volume_ul}, {max_volume_ul}]"
                )

        # A run seats two tips: one in setup, one for the second pass.
        tips_needed = 2
        if self._tip_start_index + tips_needed > self._tip_count:
            raise ValueError(
                f"not enough tips: a run needs {tips_needed} from slot "
                f"{self._tip_start_index}, but the rack has only "
                f"{self._tip_count}"
            )

        layout = self._layout
        self._tip_index = self._tip_start_index
        self._vial_totals = {}
        await self.setup()

        blue = await self.transfer_pass(
            layout.source_blue,
            [
                (layout.target_1, blue_b31_ul, "B31"),
                (layout.target_2, blue_b32_ul, "B32"),
            ],
            tag="blue",
        )
        brown = await self.transfer_pass(
            layout.source_brown,
            [
                (layout.target_1, brown_b31_ul, "B31"),
                (layout.target_2, brown_b32_ul, "B32"),
            ],
            tag="brown",
            # Final pass: discard the tip and stop — no fresh tip after.
            reload_after=False,
        )

        await self.end()
        for vial, total in self._vial_totals.items():
            logger.info("vial %s total delivered: %.4f g", vial, total)
        return {"blue": blue, "brown": brown}


# Assembled from the OPERATOR CONFIGURATION block above.
layout = CellLayout(
    travel_z_mm=travel_z_mm,
    trash_bin=Point(*trash_bin_mm),
    tip_storage=Point(*tip_storage_mm),
    tip_interval_mm=tip_interval_mm,
    tip_seat_z_mm=tip_seat_z_mm,
    source_blue=Point(*source_blue_mm),
    source_brown=Point(*source_brown_mm),
    target_1=Point(*target_1_mm),
    target_2=Point(*target_2_mm),
)


async def _demo() -> None:
    """Connect every instrument and run the full workflow once.

    Intended as a wiring smoke test on the real cell, not a unit test —
    it commands real motion and real pipetting. Fill in the OPERATOR
    CONFIGURATION block with measured coordinates first.
    """
    logging.basicConfig(level=logging.INFO)
    async with PipetteLiquidHandler(layout) as cell:
        # The balance is touched only during dispense weighing; no
        # startup query here.
        results = await cell.run()
        for liquid, dispenses in results.items():
            for item in dispenses:
                print(
                    f"{liquid} {item.label}: {item.volume_ul} uL -> "
                    f"median {item.reading.value:.4f} {item.reading.unit} "
                    f"(n={len(item.samples)})"
                )


if __name__ == "__main__":
    asyncio.run(_demo())
