"""The bus master drives the bus slave, and the pair is the measurement rig.

The interesting test is ``test_every_speed``: the same slave has to answer
correctly with the emulated CPU clock swept from slow to absurd. midiori's
equivalent logic misses cycles once ``~AS`` is low for less than about two of
its internal clocks, which is what "weak clock-up resistance" means on the box.
"""

import pytest
from amaranth import Array, Elaboratable, Module, Mux, Signal
from amaranth.sim import Simulator

from x68kmidi.bus import MIDI_BASE, BusSlave68k
from x68kmidi.busmaster import BusMaster68k

CLK = 108e6
N_REGS = 8


def slot_addr(byte_addr: int) -> int:
    """Byte address -> the A1..A23 value the slot carries."""
    return byte_addr >> 1


class Harness(Elaboratable):
    """Master and slave wired together over a shared, undriven-high data bus,
    with a trivial register file behind the slave."""

    def __init__(self, base: int = MIDI_BASE):
        self.master = BusMaster68k()
        self.slave = BusSlave68k(base)
        self.regs = Array([Signal(8, name=f"reg{i}") for i in range(N_REGS)])
        self.contention = Signal()
        self.re_count = Signal(8)
        self.we_count = Signal(8)

    def elaborate(self, platform):
        m = Module()
        m.submodules.master = ma = self.master
        m.submodules.slave = sl = self.slave

        m.d.comb += [
            sl.as_n.eq(ma.as_n),
            sl.lds_n.eq(ma.lds_n),
            sl.rw.eq(ma.rw),
            sl.iack_n.eq(ma.iack_n),
            sl.addr.eq(ma.addr),
            ma.dtack_n.eq(sl.dtack_n),
        ]

        bus = Signal(8)
        m.d.comb += [
            bus.eq(Mux(ma.data_oe, ma.data_o, Mux(sl.data_oe, sl.data_o, 0xFF))),
            ma.data_i.eq(bus),
            sl.data_i.eq(bus),
            self.contention.eq(ma.data_oe & sl.data_oe),
            sl.rdata.eq(self.regs[sl.reg_addr]),
        ]
        with m.If(sl.we):
            m.d.sync += [self.regs[sl.reg_addr].eq(sl.wdata),
                         self.we_count.eq(self.we_count + 1)]
        with m.If(sl.re):
            m.d.sync += self.re_count.eq(self.re_count + 1)
        return m


async def run_cycle(ctx, h, *, addr, read=True, data=0, iack=False,
                    s_len=5, max_wait=1000, watch=None):
    """Drive one bus cycle and return what the master observed."""
    ma = h.master
    ctx.set(ma.s_len, s_len)
    ctx.set(ma.max_wait, max_wait)
    ctx.set(ma.addr_in, addr)
    ctx.set(ma.rw_in, 1 if read else 0)
    ctx.set(ma.wdata_in, data)
    ctx.set(ma.iack_in, 1 if iack else 0)
    # Hold `start` until the master leaves IDLE: a single-tick pulse can land
    # while the previous cycle is still retiring and be missed entirely.
    ctx.set(ma.start, 1)
    await ctx.tick()
    while not ctx.get(ma.busy):
        await ctx.tick()
    ctx.set(ma.start, 0)

    for _ in range(10_000):
        if watch is not None:
            watch.append(ctx.get(h.contention))
        if ctx.get(ma.done):
            break
        await ctx.tick()
    else:
        raise AssertionError("cycle never finished")
    await ctx.tick()   # let the master settle back into IDLE

    return {
        "rdata": ctx.get(ma.rdata),
        "timeout": ctx.get(ma.timeout),
        "dtack_clocks": ctx.get(ma.dtack_clocks),
        "wait_states": ctx.get(ma.wait_states),
    }


def simulate(body, base=MIDI_BASE):
    h = Harness(base)
    sim = Simulator(h)
    sim.add_clock(1 / CLK)

    async def tb(ctx):
        await body(ctx, h)

    sim.add_testbench(tb)
    sim.run()


def test_read_returns_the_register():
    async def body(ctx, h):
        for i in range(N_REGS):
            ctx.set(h.regs[i], 0xA0 + i)
        for i in range(N_REGS):
            got = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 2 * i))
            assert got["timeout"] == 0
            assert got["rdata"] == 0xA0 + i, f"register {i}"
        assert ctx.get(h.re_count) == N_REGS
    simulate(body)


def test_write_lands_in_the_register():
    async def body(ctx, h):
        await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 2 * 5), read=False, data=0x5A)
        assert ctx.get(h.regs[5]) == 0x5A
        assert ctx.get(h.we_count) == 1, "exactly one write strobe"
        got = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 2 * 5))
        assert got["rdata"] == 0x5A
    simulate(body)


def test_silent_outside_the_window():
    async def body(ctx, h):
        for byte_addr in (MIDI_BASE - 2, MIDI_BASE + 16, 0xEAFA10, 0x000000):
            got = await run_cycle(ctx, h, addr=slot_addr(byte_addr), max_wait=3)
            assert got["timeout"] == 1, f"{byte_addr:#x} should not be decoded"
            assert got["rdata"] == 0xFF, "nobody should be driving the bus"
    simulate(body)


def test_second_card_at_eafa10():
    async def body(ctx, h):
        ctx.set(h.regs[1], 0x42)
        got = await run_cycle(ctx, h, addr=slot_addr(0xEAFA12))
        assert got["timeout"] == 0 and got["rdata"] == 0x42
        got = await run_cycle(ctx, h, addr=slot_addr(0xEAFA02), max_wait=3)
        assert got["timeout"] == 1
    simulate(body, base=0xEAFA10)


def test_iack_returns_the_vector():
    async def body(ctx, h):
        ctx.set(h.slave.vector, 0x4B)
        ctx.set(h.slave.irq_n, 0)
        got = await run_cycle(ctx, h, addr=0, iack=True)
        assert got["timeout"] == 0 and got["rdata"] == 0x4B
        # With no interrupt pending the card must stay off the bus.
        ctx.set(h.slave.irq_n, 1)
        got = await run_cycle(ctx, h, addr=0, iack=True, max_wait=3)
        assert got["timeout"] == 1 and got["rdata"] == 0xFF
    simulate(body)


@pytest.mark.parametrize("s_len", [1, 2, 3, 4, 5, 8])
def test_every_speed(s_len):
    """One S state of `s_len` master clocks == a CPU clock of CLK/(2*s_len):
    at 108 MHz that sweeps 54 MHz down to 6.75 MHz."""
    async def body(ctx, h):
        contention = []
        got = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 6), read=False,
                              data=0x3C, s_len=s_len, watch=contention)
        assert got["timeout"] == 0
        assert ctx.get(h.regs[3]) == 0x3C
        got = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 6),
                              s_len=s_len, watch=contention)
        assert got["timeout"] == 0
        assert got["rdata"] == 0x3C
        assert not any(contention), "master and slave drove the bus together"
        # Rule 2: the slave is allowed to be slow, so wait states are fine --
        # but the answer has to be bounded and the same at every speed.
        assert got["dtack_clocks"] <= 8, got["dtack_clocks"]
    simulate(body)


def test_back_to_back_cycles_never_collide():
    async def body(ctx, h):
        contention = []
        for i in range(6):
            await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 2 * (i % N_REGS)),
                            read=False, data=i, s_len=1, watch=contention)
            got = await run_cycle(ctx, h, addr=slot_addr(MIDI_BASE + 2 * (i % N_REGS)),
                                  s_len=1, watch=contention)
            assert got["rdata"] == i
        assert not any(contention)
    simulate(body)


def test_dtack_is_never_early():
    """DTACK must not assert before the data is being driven: sample both every
    clock of a read and check the ordering."""
    async def body(ctx, h):
        ctx.set(h.regs[0], 0x99)
        sl = h.slave
        ctx.set(h.master.s_len, 4)
        ctx.set(h.master.addr_in, slot_addr(MIDI_BASE))
        ctx.set(h.master.rw_in, 1)
        ctx.set(h.master.start, 1)
        await ctx.tick()
        while not ctx.get(h.master.busy):
            await ctx.tick()
        ctx.set(h.master.start, 0)
        saw_data = False
        saw_done = False
        for _ in range(200):
            oe, dtack = ctx.get(sl.data_oe), ctx.get(sl.dtack_n)
            if dtack == 0:
                assert oe == 1, "DTACK asserted while the bus was not driven"
                assert saw_data, "DTACK asserted in the same cycle as the data"
            if oe:
                saw_data = True
            if ctx.get(h.master.done):
                saw_done = True
                break
            await ctx.tick()
        assert saw_data and saw_done
    simulate(body)


def test_release_is_asynchronous():
    """Rule 4: negating the strobes takes the slave off the bus immediately,
    without waiting for a clock edge."""
    dut = BusSlave68k()
    sim = Simulator(dut)
    sim.add_clock(1 / CLK)

    async def tb(ctx):
        ctx.set(dut.rdata, 0x7E)
        ctx.set(dut.addr, slot_addr(MIDI_BASE))
        ctx.set(dut.rw, 1)
        ctx.set(dut.as_n, 0)
        ctx.set(dut.lds_n, 0)
        for _ in range(8):
            await ctx.tick()
        assert ctx.get(dut.data_oe) == 1 and ctx.get(dut.dtack_n) == 0
        assert ctx.get(dut.data_o) == 0x7E
        # No tick: the combinational release must already have happened.
        ctx.set(dut.as_n, 1)
        ctx.set(dut.lds_n, 1)
        assert ctx.get(dut.data_oe) == 0, "still driving the bus after ~AS high"
        assert ctx.get(dut.dtack_n) == 1, "still pulling ~DTACK after ~AS high"

    sim.add_testbench(tb)
    sim.run()
