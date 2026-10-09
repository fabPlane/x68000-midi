"""Sweep the emulated CPU speed and report what the slave does.

    uv run --project gateware python -m tools.sweep [master_mhz]

This is the simulation half of the measurement rig: ``BusMaster68k`` generates
68000 cycles at each speed and we record the slave's ``~DTACK`` latency, the
wait states the CPU had to insert, and whether the data survived the latch at
the end of S6. The same master, built for the Tang Console 138K and wired to a
slot breakout, does this against the real card.

Simulation has no propagation delay in it: no level translators, no PCB, no
IOB timing. Read the latency column as the *logic* cost, and add roughly
10-15 ns of real-world delay before comparing to a CPU's DTACK window.
"""

import sys

from amaranth.sim import Simulator

sys.path.insert(0, "tests")
from test_bus import Harness, run_cycle, slot_addr  # noqa: E402

from x68kmidi.bus import MIDI_BASE  # noqa: E402

S_LENS = [8, 5, 4, 3, 2, 1]


def sweep(master_hz: float):
    rows = []
    h = Harness()
    sim = Simulator(h)
    sim.add_clock(1 / master_hz)

    async def tb(ctx):
        for s_len in S_LENS:
            contention = []
            wr = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 4), read=False,
                                 data=0xC3, s_len=s_len, watch=contention)
            rd = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 4),
                                 s_len=s_len, watch=contention)
            rows.append({
                "s_len": s_len,
                "cpu_mhz": master_hz / (2 * s_len) / 1e6,
                "dtack_clocks": rd["dtack_clocks"],
                "dtack_ns": rd["dtack_clocks"] / master_hz * 1e9,
                "waits": rd["wait_states"],
                "ok": (rd["rdata"] == 0xC3 and not rd["timeout"]
                       and not wr["timeout"] and not any(contention)),
            })

    sim.add_testbench(tb)
    sim.run()
    return rows


def main(argv):
    master_mhz = float(argv[1]) if len(argv) > 1 else 108.0
    rows = sweep(master_mhz * 1e6)
    print(f"master domain {master_mhz:g} MHz\n")
    print(f"{'emulated CPU':>13}  {'S len':>5}  {'DTACK latency':>19}  "
          f"{'wait states':>11}  result")
    for r in rows:
        print(f"{r['cpu_mhz']:>10.1f} MHz  {r['s_len']:>5}  "
              f"{r['dtack_clocks']:>5} clk = {r['dtack_ns']:>5.1f} ns  "
              f"{r['waits']:>11}  {'ok' if r['ok'] else 'FAILED'}")
    print("\nlogic only: no translator, IOB or PCB delay in these numbers.")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
