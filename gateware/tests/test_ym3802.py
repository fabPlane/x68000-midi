"""The YM3802 register file: what a game writes, and what comes out of the jack.

Everything here pokes the register port the way the bus slave will, then either
reads a register back or decodes the MIDI line, so the tests say the same thing
a logic analyser on the finished card would.
"""

import pytest
from amaranth.sim import Simulator

from x68kmidi.uart import divisor
from x68kmidi.ym3802 import ACTIVE_SENSING, VERSION_STRING, Ym3802

CLK = 27_000_000          # a slower card clock keeps the simulations short
BIT = divisor(CLK)


def make(**kw):
    kw.setdefault("idle_ticks", 4000)
    kw.setdefault("tick_divisor", 8)
    return Ym3802(CLK, **kw)


def run(dut, body):
    sim = Simulator(dut)
    sim.add_clock(1 / CLK)

    async def tb(ctx):
        await body(ctx)

    sim.add_testbench(tb)
    sim.run()


async def write(ctx, dut, reg, value):
    ctx.set(dut.reg_addr, reg)
    ctx.set(dut.wdata, value)
    ctx.set(dut.we, 1)
    await ctx.tick()
    ctx.set(dut.we, 0)
    await ctx.tick()


async def write_grouped(ctx, dut, reg_num, value):
    """`reg_num` is group<<4 | reg, as the datasheet numbers them."""
    await write(ctx, dut, 1, reg_num >> 4)
    await write(ctx, dut, reg_num & 7, value)


async def read(ctx, dut, reg):
    ctx.set(dut.reg_addr, reg)
    await ctx.tick()
    return ctx.get(dut.rdata)


async def read_grouped(ctx, dut, reg_num):
    await write(ctx, dut, 1, reg_num >> 4)
    return await read(ctx, dut, reg_num & 7)


async def capture_midi(ctx, dut, n_bytes, limit=40_000):
    """Decode `n_bytes` off the MIDI line, re-syncing on each start bit.

    Back-to-back bytes have no gap between one stop bit and the next start bit,
    so the decoder waits for the line to be idle and then for a falling edge
    rather than counting bit periods through the boundary."""
    out = []
    for _ in range(n_bytes):
        for _ in range(limit):              # end of any byte in flight
            if ctx.get(dut.tx) == 1:
                break
            await ctx.tick()
        for _ in range(limit):              # falling edge = start bit
            if ctx.get(dut.tx) == 0:
                break
            await ctx.tick()
        else:
            raise AssertionError(f"no start bit; got {out}")
        for _ in range(BIT // 2):           # middle of the start bit
            await ctx.tick()
        assert ctx.get(dut.tx) == 0, "start bit"
        value = 0
        for i in range(8):
            for _ in range(BIT):
                await ctx.tick()
            value |= ctx.get(dut.tx) << i
        for _ in range(BIT):
            await ctx.tick()
        assert ctx.get(dut.tx) == 1, "stop bit"
        out.append(value)
    return out


def test_fifo_byte_reaches_the_midi_line():
    note_on = [0x90, 0x3C, 0x7F]

    async def body(ctx):
        await write(ctx, dut, 1, 5)              # group 5
        for byte in note_on:
            await write(ctx, dut, 6, byte)       # 0x56: transmit FIFO
        assert await capture_midi(ctx, dut, 3) == note_on

    dut = make()
    run(dut, body)


def test_transmit_status():
    async def body(ctx):
        tsr = await read_grouped(ctx, dut, 0x54)
        assert tsr & 0x80, "TXEMP should be set when the FIFO is empty"
        assert tsr & 0x40, "TXRDY should be set when the FIFO has room"
        await write(ctx, dut, 1, 5)
        # The transmitter drains while we fill, so write until it says full
        # rather than assuming a byte count.
        for i in range(32):
            await write(ctx, dut, 6, i)
            if not await read_grouped(ctx, dut, 0x54) & 0x40:
                break
            await write(ctx, dut, 1, 5)
        tsr = await read_grouped(ctx, dut, 0x54)
        assert not tsr & 0x80, "TXEMP should be clear with bytes queued"
        assert not tsr & 0x40, "TXRDY should be clear when the FIFO is full"

    dut = make()
    run(dut, body)


@pytest.mark.parametrize("reg_num,expect", [(0x34, 0x04), (0x36, 0x00),
                                            (0x64, 0xA0), (0x74, 0x00),
                                            (0x96, 0xFF)])
def test_probe_constants(reg_num, expect):
    """Drivers identify the chip by these; midiori returns exactly these."""
    async def body(ctx):
        assert await read_grouped(ctx, dut, reg_num) == expect

    dut = make()
    run(dut, body)


def test_version_string():
    async def body(ctx):
        got = bytearray()
        for i in range(len(VERSION_STRING) + 1):
            await write_grouped(ctx, dut, 0xF4, i)
            got.append(await read(ctx, dut, 5))   # 0xF5
        assert got[-1] == 0, "null terminated"
        assert bytes(got[:-1]) == VERSION_STRING

    dut = make()
    run(dut, body)


def test_group_register_banks_the_window():
    async def body(ctx):
        await write(ctx, dut, 1, 3)
        assert await read(ctx, dut, 4) == 0x04    # 0x34
        await write(ctx, dut, 1, 6)
        assert await read(ctx, dut, 4) == 0xA0    # 0x64
        await write(ctx, dut, 1, 9)
        assert await read(ctx, dut, 6) == 0xFF    # 0x96

    dut = make()
    run(dut, body)


def test_tx_empty_interrupt_and_vector():
    async def body(ctx):
        assert ctx.get(dut.irq_n) == 1, "no interrupt before one is enabled"
        await write_grouped(ctx, dut, 0x04, 0b101 << 5)   # IVO = 5
        await write(ctx, dut, 6, 1 << 6)                  # IER: TX empty only
        await ctx.tick()
        assert ctx.get(dut.irq_n) == 0, "FIFO is empty, so TXEMP should fire"
        # vector = IVO<<5 | n<<4 | index<<1
        assert ctx.get(dut.vector) == (5 << 5) | (6 << 1)
        assert await read(ctx, dut, 0) == ctx.get(dut.vector), "register 0 is IVR"
        assert await read(ctx, dut, 2) & (1 << 6), "ISR bit 6"
        # Writing a 1 to a bit of register 3 clears it.
        await write(ctx, dut, 3, 1 << 6)
        assert ctx.get(dut.irq_n) == 1
        assert not await read(ctx, dut, 2) & (1 << 6)

    dut = make()
    run(dut, body)


def test_queueing_a_byte_clears_the_tx_empty_interrupt():
    async def body(ctx):
        await write(ctx, dut, 6, 1 << 6)         # IER
        await ctx.tick()
        assert ctx.get(dut.irq_n) == 0
        await write(ctx, dut, 1, 5)
        await write(ctx, dut, 6, 0x90)           # into the FIFO
        await ctx.tick()
        assert ctx.get(dut.irq_n) == 1, "a queued byte should drop the request"

    dut = make()
    run(dut, body)


def test_active_sensing_when_enabled():
    """With ASE set and nothing to send, the card emits 0xFE forever, which is
    how a synth knows the cable is still there."""
    async def body(ctx):
        await write_grouped(ctx, dut, 0x14, 1 << 5)
        assert await capture_midi(ctx, dut, 2, limit=60_000) == \
            [ACTIVE_SENSING, ACTIVE_SENSING]

    dut = make()
    run(dut, body)


def test_no_active_sensing_by_default():
    async def body(ctx):
        for _ in range(12_000):
            assert ctx.get(dut.tx) == 1, "the line must stay idle"
            await ctx.tick()

    dut = make()
    run(dut, body)


def test_break_pulls_the_line_low():
    async def body(ctx):
        await write_grouped(ctx, dut, 0x55, 1 << 3)       # BRKE
        await ctx.tick()
        assert ctx.get(dut.tx) == 0
        await write_grouped(ctx, dut, 0x55, 0)
        await ctx.tick()
        assert ctx.get(dut.tx) == 1

    dut = make()
    run(dut, body)


def test_general_purpose_timer_interrupt():
    async def body(ctx):
        await write_grouped(ctx, dut, 0x84, 20)           # reload low byte
        await write_grouped(ctx, dut, 0x85, 0x80)         # high byte + load now
        await write_grouped(ctx, dut, 0x06, 1 << 7)       # IER: GPT
        for _ in range(8 * 40):
            if ctx.get(dut.irq_n) == 0:
                break
            await ctx.tick()
        assert ctx.get(dut.irq_n) == 0, "GPT should have fired"
        assert await read(ctx, dut, 2) & (1 << 7)

    dut = make()
    run(dut, body)
