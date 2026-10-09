"""Run our bus slave and the midiori reference model on the same rig.

    uv run --project gateware python -m tools.compare [master_mhz]

For each emulated CPU speed, each model is given a write followed by a read, at
every phase of its own clock relative to the bus (the pattern repeats every
``master_hz / gcd`` master clocks, so the sweep is exhaustive). Reported per
model and speed:

* **ok** -- the read returned what the write stored, and the cycle completed.
* **wait** -- wait states the CPU had to insert, worst case over phases. This is
  the honest cost of a slow slave and is *not* a failure.
* **clash** -- phases where the slave was still driving the data bus while the
  master drove it: a real fault, and the one that corrupts unrelated transfers.

Remember what simulation leaves out, in the reference model's favour: no level
translator or PCB delay, no combinational mux delay on its read path, and no
metastability from sampling an asynchronous ``~AS`` without synchronisers.
"""

import sys
from fractions import Fraction

from amaranth.sim import Simulator

from x68kmidi.bus import MIDI_BASE, BusSlave68k
from x68kmidi.midiori_ref import MIDIORI_CLK_HZ, MidioriBusSlave
from x68kmidi.sim_harness import Harness, run_cycle, slot_addr

S_LENS = [8, 5, 4, 3, 2, 1]


def phase_count(master_hz: float, ref_hz: float) -> int:
    """Master clocks after which the two clocks line up again."""
    return Fraction(master_hz / ref_hz).limit_denominator(1000).numerator


def measure(master_hz, *, reference, s_lens=S_LENS, max_wait=1000,
            read_only=False, out_delay=0, data_delay=None, dtack_delay=None):
    """Return {s_len: dict} for one model."""
    if reference:
        h = Harness(slave=MidioriBusSlave(MIDI_BASE, domain="ref"),
                    reg_domain="ref", out_delay=out_delay,
                    data_delay=data_delay, dtack_delay=dtack_delay)
        phases = phase_count(master_hz, MIDIORI_CLK_HZ)
    else:
        h = Harness(out_delay=out_delay, data_delay=data_delay,
                    dtack_delay=dtack_delay)
        phases = 1          # synchronous with the rig; phase is not a variable

    sim = Simulator(h)
    sim.add_clock(1 / master_hz)
    if reference:
        sim.add_clock(1 / MIDIORI_CLK_HZ, domain="ref")
    out = {}

    async def tb(ctx):
        for s_len in s_lens:
            bad = clash = 0
            worst_wait = 0
            for phase in range(phases):
                for _ in range(phase):
                    await ctx.tick()
                watch = []
                if read_only:
                    # Seed the register with a generous cycle, then time the
                    # read on its own: a 68000 asserts its data strobe on a
                    # write only in S4, so writes always cost a wait state and
                    # would mask the read's margin.
                    await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 4),
                                    read=False, data=0xC3, s_len=8)
                    wr = {"timeout": 0, "wait_states": 0}
                else:
                    wr = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 4),
                                         read=False, data=0xC3, s_len=s_len,
                                         max_wait=max_wait, watch=watch)
                rd = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 4),
                                     s_len=s_len, max_wait=max_wait, watch=watch)
                if rd["rdata"] != 0xC3 or rd["timeout"] or wr["timeout"]:
                    bad += 1
                if any(watch):
                    clash += 1
                worst_wait = max(worst_wait, rd["wait_states"], wr["wait_states"])
            out[s_len] = {"phases": phases, "bad": bad, "clash": clash,
                          "wait": worst_wait}

    sim.add_testbench(tb)
    sim.run()
    return out


def zero_wait_ceiling(master_hz, *, reference, out_delay=0):
    """Fastest emulated CPU for which a read still completes with no wait
    states -- i.e. with DTACK ready by the end of S4. A delay-free proxy for
    how much margin the slave has before the CPU has to wait for it."""
    best = None
    for s_len in sorted(S_LENS):            # fastest CPU first
        r = measure(master_hz, reference=reference, s_lens=[s_len],
                    max_wait=0, read_only=True, out_delay=out_delay)
        if r[s_len]["bad"] == 0:
            best = master_hz / (2 * s_len) / 1e6
            break
    return best


def main(argv):
    master_mhz = float(argv[1]) if len(argv) > 1 else 108.0
    out_delay = int(argv[2]) if len(argv) > 2 else 0
    data_delay = int(argv[3]) if len(argv) > 3 else None
    master_hz = master_mhz * 1e6
    kw = dict(out_delay=out_delay, data_delay=data_delay)
    ours = measure(master_hz, reference=False, **kw)
    theirs = measure(master_hz, reference=True, **kw)

    print(f"master/instrument domain {master_mhz:g} MHz; "
          f"ours at {master_mhz:g} MHz, midiori model at "
          f"{MIDIORI_CLK_HZ / 1e6:g} MHz")
    dd = out_delay if data_delay is None else data_delay
    print(f"output delay: DTACK {out_delay} clk "
          f"({out_delay / master_hz * 1e9:.1f} ns), data {dd} clk "
          f"({dd / master_hz * 1e9:.1f} ns), both slaves\n")
    print(f"{'emulated CPU':>13} | {'ours: ok  wait  clash':>23} | "
          f"{'midiori: ok      wait  clash':>30}")
    print("-" * 72)
    for s_len in S_LENS:
        cpu = master_hz / (2 * s_len) / 1e6
        o, t = ours[s_len], theirs[s_len]
        o_ok = f"{o['phases'] - o['bad']}/{o['phases']}"
        t_ok = f"{t['phases'] - t['bad']}/{t['phases']}"
        print(f"{cpu:>10.1f} MHz | {o_ok:>8}  {o['wait']:>4}  {o['clash']:>5} | "
              f"{t_ok:>12}  {t['wait']:>4}  {t['clash']:>5}")
    o_ceil = zero_wait_ceiling(master_hz, reference=False, out_delay=out_delay)
    t_ceil = zero_wait_ceiling(master_hz, reference=True, out_delay=out_delay)
    fmt = lambda c: f"{c:.1f} MHz" if c else "none of the speeds tried"
    print(f"\nfastest zero-wait-state read: ours {fmt(o_ceil)}, "
          f"midiori model {fmt(t_ceil)}")
    print("\nok = phases where the data survived; wait = worst-case wait states "
          "the CPU\ninserted; clash = phases with both sides driving the data bus.")
    print("Simulation has no translator, PCB or mux delay and no metastability, "
          "all of\nwhich flatter the reference model.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
