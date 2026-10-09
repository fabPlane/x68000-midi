"""End to end: 68000 bus cycles in, MIDI bytes out.

This is the test that stands in for "plug it into an X68000 and run a game" --
everything a driver does goes through the real bus slave, the real register
file and the real UART, with the bus master generating proper 68000 timing.
"""

import pytest
from amaranth import Elaboratable, Module, Mux, Signal
from amaranth.sim import Simulator

from x68kmidi.busmaster import BusMaster68k
from x68kmidi.card import PORT_BASES, Card
from x68kmidi.uart import divisor
from x68kmidi.sim_harness import slot_addr

CLK = 27_000_000          # a slower card clock keeps the simulation short
BIT = divisor(CLK)
S_LEN = 3                 # ~4.5 MHz emulated CPU; the ratio is what matters


class CardRig(Elaboratable):
    def __init__(self):
        self.master = BusMaster68k()
        self.card = Card(CLK, idle_ticks=4000, tick_divisor=8)
        self.contention = Signal()

    def elaborate(self, platform):
        m = Module()
        m.submodules.master = ma = self.master
        m.submodules.card = c = self.card
        bus = Signal(8)
        m.d.comb += [
            c.as_n.eq(ma.as_n), c.lds_n.eq(ma.lds_n), c.rw.eq(ma.rw),
            c.addr.eq(ma.addr), c.iack2_n.eq(ma.iack_n),
            bus.eq(Mux(ma.data_oe, ma.data_o, Mux(c.data_oe, c.data_o, 0xFF))),
            ma.data_i.eq(bus), c.data_i.eq(bus),
            ma.dtack_n.eq(c.dtack_n),
            self.contention.eq(ma.data_oe & c.data_oe),
        ]
        return m


async def cycle(ctx, rig, *, addr, read=True, data=0, iack=False, s_len=S_LEN):
    ma = rig.master
    ctx.set(ma.s_len, s_len)
    ctx.set(ma.max_wait, 1000)
    ctx.set(ma.addr_in, addr)
    ctx.set(ma.rw_in, 1 if read else 0)
    ctx.set(ma.wdata_in, data)
    ctx.set(ma.iack_in, 1 if iack else 0)
    ctx.set(ma.start, 1)
    await ctx.tick()
    while not ctx.get(ma.busy):
        await ctx.tick()
    ctx.set(ma.start, 0)
    for _ in range(100_000):
        assert not ctx.get(rig.contention), "two drivers on the data bus"
        if ctx.get(ma.done):
            break
        await ctx.tick()
    else:
        raise AssertionError("cycle never finished")
    await ctx.tick()
    assert not ctx.get(ma.timeout), f"the card did not answer {addr:#x}"
    return ctx.get(ma.rdata)


async def poke(ctx, rig, port, reg, value):
    await cycle(ctx, rig, addr=slot_addr(PORT_BASES[port] + 2 * reg),
                read=False, data=value)


async def peek(ctx, rig, port, reg):
    return await cycle(ctx, rig, addr=slot_addr(PORT_BASES[port] + 2 * reg))


async def send(ctx, rig, port, data):
    """What a driver does: select group 5, then write the FIFO register."""
    await poke(ctx, rig, port, 1, 5)
    for byte in data:
        await poke(ctx, rig, port, 6, byte)


async def capture(ctx, line, n_bytes, limit=200_000):
    out = []
    for _ in range(n_bytes):
        for _ in range(limit):
            if ctx.get(line) == 1:
                break
            await ctx.tick()
        for _ in range(limit):
            if ctx.get(line) == 0:
                break
            await ctx.tick()
        else:
            raise AssertionError(f"no start bit; got {out}")
        for _ in range(BIT // 2):
            await ctx.tick()
        value = 0
        for i in range(8):
            for _ in range(BIT):
                await ctx.tick()
            value |= ctx.get(line) << i
        for _ in range(BIT):
            await ctx.tick()
        assert ctx.get(line) == 1, "stop bit"
        out.append(value)
    return out


def run(body):
    rig = CardRig()
    sim = Simulator(rig)
    sim.add_clock(1 / CLK)

    async def tb(ctx):
        await body(ctx, rig)

    sim.add_testbench(tb)
    sim.run()


def test_a_note_comes_out_of_the_jack():
    note_on = [0x90, 0x3C, 0x7F]

    async def body(ctx, rig):
        await send(ctx, rig, 0, note_on)
        assert await capture(ctx, rig.card.midi_tx, 3) == note_on

    run(body)


def decode(samples):
    """Software UART over a recorded line: both ports transmit at once, so they
    have to be sampled together and decoded afterwards."""
    out, i, n = [], 0, len(samples)
    while True:
        while i < n and samples[i] == 0:    # wait for idle
            i += 1
        while i < n and samples[i] == 1:    # falling edge
            i += 1
        if i + 10 * BIT >= n:
            return out
        mid = i + BIT // 2
        value = sum(samples[mid + (bit + 1) * BIT] << bit for bit in range(8))
        assert samples[mid + 9 * BIT] == 1, "stop bit"
        out.append(value)
        i = mid + 9 * BIT


def test_the_two_ports_are_independent():
    async def body(ctx, rig):
        await send(ctx, rig, 1, [0xC0, 0x30])     # program change on OUT 2
        await send(ctx, rig, 0, [0x90, 0x40])     # note on OUT 1
        one, two = [], []
        for _ in range(24 * BIT):
            one.append(ctx.get(rig.card.midi_tx))
            two.append(ctx.get(rig.card.midi_tx2))
            await ctx.tick()
        assert decode(one) == [0x90, 0x40]
        assert decode(two) == [0xC0, 0x30]

    run(body)


@pytest.mark.parametrize("port", [0, 1])
def test_the_driver_probe_sequence(port):
    """What a driver reads to decide a YM3802 is there."""
    async def body(ctx, rig):
        await poke(ctx, rig, port, 1, 3)
        assert await peek(ctx, rig, port, 4) == 0x04
        await poke(ctx, rig, port, 1, 6)
        assert await peek(ctx, rig, port, 4) == 0xA0
        await poke(ctx, rig, port, 1, 9)
        assert await peek(ctx, rig, port, 6) == 0xFF
        await poke(ctx, rig, port, 1, 5)
        assert await peek(ctx, rig, port, 4) & 0x80, "TXEMP at reset"

    run(body)


def test_interrupt_and_acknowledge_over_the_bus():
    async def body(ctx, rig):
        card = rig.card
        assert ctx.get(card.irq2_n) == 1
        await poke(ctx, rig, 0, 1, 0)             # group 0
        await poke(ctx, rig, 0, 4, 0b011 << 5)    # IVO = 3
        await poke(ctx, rig, 0, 6, 1 << 6)        # IER: TX empty
        await ctx.tick()
        assert ctx.get(card.irq2_n) == 0, "TXEMP should raise IRQ2"
        vector = await cycle(ctx, rig, addr=0, iack=True)
        assert vector == (3 << 5) | (6 << 1), f"vector {vector:#04x}"
        await poke(ctx, rig, 0, 3, 1 << 6)        # clear the ISR bit
        await ctx.tick()
        assert ctx.get(card.irq2_n) == 1

    run(body)


def test_nothing_else_on_the_bus_is_touched():
    """Addresses outside both windows must get no DTACK and no data."""
    async def body(ctx, rig):
        ma = rig.master
        for byte_addr in (0xEAF9FE, 0xEAFA20, 0xE9A001, 0x000000, 0xECE000):
            ctx.set(ma.s_len, S_LEN)
            ctx.set(ma.max_wait, 3)
            ctx.set(ma.addr_in, slot_addr(byte_addr))
            ctx.set(ma.rw_in, 1)
            ctx.set(ma.iack_in, 0)
            ctx.set(ma.start, 1)
            await ctx.tick()
            while not ctx.get(ma.busy):
                await ctx.tick()
            ctx.set(ma.start, 0)
            while not ctx.get(ma.done):
                assert ctx.get(rig.card.data_oe) == 0, f"drove the bus at {byte_addr:#x}"
                await ctx.tick()
            await ctx.tick()
            assert ctx.get(ma.timeout) == 1, f"answered {byte_addr:#x}"

    run(body)
