"""Run the full workflow with every Z coordinate shifted by -20 mm.

Identical to ``python pipette_liquid_handler.py`` except that all Z
values are shifted by ``DZ`` (the travel height and every station's
descend depth) — i.e. the tip goes 20 mm less deep at each station. X
positions, tip interval, and the relative tip-seat press depth are
unchanged. The shifted layout is derived from the module's real
``layout`` so it stays in sync with the OPERATOR CONFIGURATION block —
only Z differs.

Each shifted Z is floored at 0: the stage cannot rise above the homed
top, and the driver rejects negative millimeters, so travel_z (0)
stays 0 here.

Run (lh env):
    /opt/conda/envs/lh/bin/python claude_test/run_z_minus20.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipette_liquid_handler import (  # noqa: E402
    CellLayout,
    PipetteLiquidHandler,
    Point,
    layout as base_layout,
)

DZ = -20  # millimeters added to every Z coordinate for this test


def _shift_z(value):
    """Return ``value`` + DZ, floored at 0 (no negative stage mm)."""
    return max(0, value + DZ)


def _shift(point):
    """Return ``point`` with its Z shifted by DZ (X unchanged)."""
    return Point(x_mm=point.x_mm, z_mm=_shift_z(point.z_mm))


# Same as the real layout, but every Z shifted by DZ. tip_seat_z_mm is a
# relative press depth, not a Z coordinate, so it is left unchanged.
test_layout = CellLayout(
    travel_z_mm=_shift_z(base_layout.travel_z_mm),
    trash_bin=_shift(base_layout.trash_bin),
    tip_storage=_shift(base_layout.tip_storage),
    tip_interval_mm=base_layout.tip_interval_mm,
    tip_seat_z_mm=base_layout.tip_seat_z_mm,
    source_blue=_shift(base_layout.source_blue),
    source_brown=_shift(base_layout.source_brown),
    target_1=_shift(base_layout.target_1),
    target_2=_shift(base_layout.target_2),
)


async def main():
    logging.basicConfig(level=logging.INFO)
    print(f"=== Z{DZ:+d}mm test layout (floored at 0) ===")
    print(f"  travel_z = {test_layout.travel_z_mm}")
    for name in ("trash_bin", "tip_storage", "source_blue",
                 "source_brown", "target_1", "target_2"):
        print(f"  {name:12s} = {getattr(test_layout, name)}")

    # The workflow logs each step (setup / aspirate / move+dispense /
    # weigh / tip swap) at INFO; the balance is read only at dispense.
    async with PipetteLiquidHandler(test_layout) as cell:
        results = await cell.run()

    print("\n=== results (median weights) ===")
    for liquid, dispenses in results.items():
        for item in dispenses:
            print(
                f"{liquid} {item.label}: {item.volume_ul} uL -> "
                f"{item.reading.value:.4f} {item.reading.unit} "
                f"(n={len(item.samples)})"
            )


if __name__ == "__main__":
    asyncio.run(main())
