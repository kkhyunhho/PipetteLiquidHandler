"""Real-hardware comms bring-up: open all three devices, no motion.

Verifies the integrated module talks to the actual pipette, balance, and
stage adapters without commanding any motion: connect(home=False) opens
everything, then only info queries are issued. No homing, no moves, no
enable_motor_control (which would eject a tip), no dispensing.

Run (lh env):  /opt/conda/envs/lh/bin/python claude_test/bringup_comms.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipette_liquid_handler import PipetteLiquidHandler, layout


async def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cell = PipetteLiquidHandler(layout)
    try:
        # No homing / no motion — communications check only.
        await cell.connect(home=False)
        print("\n=== connected (no motion) ===")

        # Balance (P.S.) — instant SBI ID query.
        try:
            model = await cell.get_balance_model()
            print(f"[P.S.] balance model : {model!r}")
        except Exception as e:
            print(f"[P.S.] query FAILED  : {e!r}")

        # Pipette (A.P.) — info queries, no motor control needed.
        ap = cell._require_pipette()
        for label, coro in (
            ("version", ap.get_version()),
            ("model", ap.get_model()),
            ("nominal_uL", ap.get_nominal_volume()),
        ):
            try:
                print(f"[A.P.] {label:10s}: {await coro}")
            except Exception as e:
                print(f"[A.P.] {label:10s}: FAILED {e!r}")

        # Stage (C.M.) — read IO on each motor (no motion).
        for m in cell._z_motors + [cell._x_motor]:
            try:
                io = m._read_io_status()
                print(f"[C.M.] motor {m.can_id} IO status: {io}")
            except Exception as e:
                print(f"[C.M.] motor IO read FAILED: {e!r}")

    finally:
        await cell.close()
        print("=== closed ===")


if __name__ == "__main__":
    asyncio.run(main())
