"""The midiori reference model, and what it actually costs to be built that way.

Two jobs here. First, prove the port is faithful enough to be worth comparing
against: it has to work, at every phase of its 16 MHz clock against the bus, at
the speeds a stock X68000 runs. Second, pin the measured difference between it
and ours so a regression shows up -- see ``tools/compare.py`` for the full
table and ``docs/DECISIONS.md`` for what the numbers did and did not support.
"""

import pytest
from amaranth.sim import Simulator

from x68kmidi.bus import MIDI_BASE
from x68kmidi.midiori_ref import MIDIORI_CLK_HZ, MidioriBusSlave
from x68kmidi.sim_harness import Harness, run_cycle, slot_addr

CLK = 108e6
PHASES = 9          # a sample of the 27 that cover every clock alignment


def reference_harness():
    return Harness(slave=MidioriBusSlave(MIDI_BASE, domain="ref"), reg_domain="ref")


def run(h, body, ref=False):
    sim = Simulator(h)
    sim.add_clock(1 / CLK)
    if ref:
        sim.add_clock(1 / MIDIORI_CLK_HZ, domain="ref")

    async def tb(ctx):
        await body(ctx, h)

    sim.add_testbench(tb)
    sim.run()


async def write_then_read(ctx, h, s_len, phase):
    """Return True if the byte written came back."""
    for _ in range(phase):
        await ctx.tick()
    watch = []
    wr = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 4), read=False,
                         data=0xC3, s_len=s_len, watch=watch)
    rd = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 4), s_len=s_len,
                         watch=watch)
    return (not wr["timeout"] and not rd["timeout"]
            and rd["rdata"] == 0xC3 and not any(watch))


@pytest.mark.parametrize("s_len,cpu", [(8, "6.8 MHz"), (5, "10.8 MHz"), (3, "18 MHz")])
def test_reference_works_at_real_machine_speeds(s_len, cpu):
    """A faithful port has to pass where the real card is known to work: every
    stock X68000 is at or below 16 MHz."""
    results = []

    async def body(ctx, h):
        for phase in range(PHASES):
            results.append(await write_then_read(ctx, h, s_len, phase))

    run(reference_harness(), body, ref=True)
    assert all(results), f"reference model failed at {cpu}: {results}"


def test_reference_breaks_where_ours_does_not():
    """At an emulated 54 MHz the reference model cannot complete a transfer at
    any clock phase, while ours still does. This is the measured half of the
    timing argument; the unmeasured half is metastability, which simulation
    resolves cleanly and hardware does not."""
    ref_results, our_results = [], []

    async def ref_body(ctx, h):
        for phase in range(PHASES):
            ref_results.append(await write_then_read(ctx, h, 1, phase))

    async def our_body(ctx, h):
        for phase in range(PHASES):
            our_results.append(await write_then_read(ctx, h, 1, phase))

    run(reference_harness(), ref_body, ref=True)
    run(Harness(), our_body)

    assert all(our_results), "our slave should survive an absurd bus speed"
    assert not any(ref_results), "reference model unexpectedly kept up"


def test_reference_holds_the_bus_after_the_cycle():
    """Rule 4, from the other side: with no asynchronous release the model is
    still driving data when ~AS goes high, and only lets go on its own clock
    edge. Ours drops the bus in the same delta (see test_release_is_asynchronous)."""
    sl = MidioriBusSlave(MIDI_BASE, domain="ref")
    sim = Simulator(sl)
    sim.add_clock(1 / MIDIORI_CLK_HZ, domain="ref")

    async def tb(ctx):
        ctx.set(sl.rdata, 0x7E)
        ctx.set(sl.addr, slot_addr(MIDI_BASE))
        ctx.set(sl.rw, 1)
        ctx.set(sl.as_n, 0)
        ctx.set(sl.lds_n, 0)
        for _ in range(3):
            await ctx.tick(domain="ref")
        assert ctx.get(sl.data_oe) == 1 and ctx.get(sl.dtack_n) == 0
        ctx.set(sl.as_n, 1)
        ctx.set(sl.lds_n, 1)
        assert ctx.get(sl.data_oe) == 1, "model is expected to still drive the bus"
        await ctx.tick(domain="ref")
        assert ctx.get(sl.data_oe) == 0, "it should let go on the next edge"

    sim.add_testbench(tb)
    sim.run()
