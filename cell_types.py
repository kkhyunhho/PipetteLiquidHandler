"""Domain value types for the PipetteLiquidHandler cell.

Geometry (:class:`Point`, :class:`CellLayout`) and the dispense outcome
(:class:`DispenseResult`), split out of ``pipette_liquid_handler.py`` so
the controller module holds orchestration rather than data definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from entris_ii import WeightReading


class Point(NamedTuple):
    """An absolute XZ stage position in millimeters from the origin.

    Attributes:
        x_mm: Horizontal (X) target in millimeters.
        z_mm: Vertical (Z) descend depth in millimeters; both Z motors
            move together to this height for the station's action.
    """

    x_mm: int
    z_mm: int


@dataclass(frozen=True)
class CellLayout:
    """Physical coordinates and tip geometry for one bench setup.

    All values are bench-specific and must be measured for the real
    cell. The Z component of each :class:`Point` is the descend depth
    where that station's action happens; the tip traverses in X at
    ``travel_z_mm`` (raised clear) and only then descends to it.

    Attributes:
        travel_z_mm: Safe clearance height for X traversal. Z is raised
            to this between stations so the tip clears all labware.
        trash_bin: T.B. — drop position for used tips.
        tip_storage: T.S. — position over tip #0; ``z_mm`` is the
            descend/approach height onto the rack.
        tip_interval_mm: Center-to-center spacing between adjacent tips
            in the rack, applied along X.
        tip_seat_z_mm: Extra Z press past ``tip_storage.z_mm`` to seat a
            new tip; the seat target is ``tip_storage.z_mm`` +
            ``tip_seat_z_mm``.
        source_blue: B1 — vial of blue liquid.
        source_brown: B2 — vial of brown liquid.
        target_1: B31 — first target vial, sitting on the balance pan.
        target_2: B32 — second target vial, sitting on the balance pan.
    """

    travel_z_mm: int
    trash_bin: Point
    tip_storage: Point
    tip_interval_mm: int
    tip_seat_z_mm: int
    source_blue: Point
    source_brown: Point
    target_1: Point
    target_2: Point


class DispenseResult(NamedTuple):
    """One dispense-and-weigh outcome from the closed-loop operation.

    Attributes:
        label: Target identifier (e.g. ``"B31"``).
        volume_ul: Nominal dispensed volume in microliters.
        reading: Final balance reading — the median of ``samples`` —
            taken after the dispense with the tip raised clear.
        samples: The individual stable reads the median was taken from.
    """

    label: str
    volume_ul: int
    reading: WeightReading
    samples: list[WeightReading]
