"""The transmitter must put a MIDI byte on the wire as 8N1 at 31 250 bps."""

import pytest
from amaranth.sim import Simulator

from x68kmidi.uart import UartTx, divisor

CLK = 27_000_000
BIT = divisor(CLK)  # 864 clocks per bit at 27 MHz


def _send(dut, data, clk=CLK):
    trace = []

    async def tb(ctx):
        # present the byte; the transmitter latches it while idle
        ctx.set(dut.tx_data, data)
        ctx.set(dut.tx_ready, 1)
        assert ctx.get(dut.tx_ack) == 1
        await ctx.tick()
        ctx.set(dut.tx_ready, 0)
        # record the line for 12 bit periods
        for _ in range(12 * BIT):
            trace.append(ctx.get(dut.tx))
            await ctx.tick()

    sim = Simulator(dut)
    sim.add_clock(1 / clk)
    sim.add_testbench(tb)
    sim.run()
    return trace


def _decode(trace):
    """Find the start bit, then sample the middle of each following bit."""
    start = trace.index(0)
    samples = [trace[start + BIT * n + BIT // 2] for n in range(10)]
    assert samples[0] == 0, "start bit"
    assert samples[9] == 1, "stop bit"
    return sum(b << i for i, b in enumerate(samples[1:9])), start


@pytest.mark.parametrize("byte", [0x90, 0x3C, 0x7F, 0xFE, 0x00, 0xFF])
def test_byte_on_the_wire(byte):
    dut = UartTx(CLK)
    trace = _send(dut, byte)
    value, start = _decode(trace)
    assert value == byte
    # midiori timing: start bit one bit period after the latch
    assert start == BIT
    # line idles high before the start bit and after the stop bit
    assert all(trace[:start])
    assert all(trace[start + 10 * BIT:])


def test_divisor_is_exact_at_27mhz():
    assert divisor(27_000_000) == 864 and 27_000_000 % 31_250 == 0


def test_divisor_rejects_bad_clock():
    with pytest.raises(ValueError):
        divisor(40_000)  # 1.28 -> 28 % error
