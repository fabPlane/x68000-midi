"""MIDI UART transmitter: 31 250 bps, 8N1, LSB first.

Port of midiori's ``UART`` (reference/midiori/gateware/midiori.py, Migen, CC0)
with the same handshake so the YM3802 transmit FIFO wrapper ports unchanged:

* ``tx_ready`` (in): a byte is available on ``tx_data``.
* ``tx_ack`` (out): high while idle; the byte on ``tx_data`` is latched on the
  cycle ``tx_ready & tx_ack`` and the transmitter leaves idle, so a FIFO can use
  ``tx_ack`` directly as its read-enable, exactly as midiori does.

Timing matches midiori: the start bit begins one bit period after the byte is
latched, and the stop bit lasts one full bit period before ``tx_ack`` returns.
"""

from amaranth import Module, Signal, Cat
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

__all__ = ["UartTx", "divisor"]

MIDI_BAUD = 31_250


def divisor(clk_freq: int, baud: int = MIDI_BAUD, max_ppm: float = 50_000) -> int:
    """Integer clock divisor for ``baud``; refuses if the error exceeds max_ppm."""
    div = clk_freq // baud
    if div <= 0:
        raise ValueError("baud rate is too high for this clock")
    ppm = 1_000_000 * ((clk_freq / div) - baud) / baud
    if ppm > max_ppm:
        raise ValueError(f"baud error {ppm:.0f} ppm exceeds {max_ppm} ppm")
    return div


class UartTx(wiring.Component):
    tx_data: In(8)
    tx_ready: In(1)
    tx_ack: Out(1)
    tx: Out(1, init=1)

    def __init__(self, clk_freq: int = 27_000_000, baud: int = MIDI_BAUD):
        self.divisor = divisor(clk_freq, baud)
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        div = self.divisor
        counter = Signal(range(div))
        strobe = Signal()
        bitno = Signal(3)
        latch = Signal(8)

        m.d.comb += strobe.eq(counter == 0)
        with m.If(counter == 0):
            m.d.sync += counter.eq(div - 1)
        with m.Else():
            m.d.sync += counter.eq(counter - 1)

        with m.FSM(init="IDLE"):
            with m.State("IDLE"):
                m.d.comb += self.tx_ack.eq(1)
                with m.If(self.tx_ready):
                    m.d.sync += [counter.eq(div - 1), latch.eq(self.tx_data), bitno.eq(0)]
                    m.next = "START"
                with m.Else():
                    m.d.sync += self.tx.eq(1)
            with m.State("START"):
                with m.If(strobe):
                    m.d.sync += self.tx.eq(0)
                    m.next = "DATA"
            with m.State("DATA"):
                with m.If(strobe):
                    m.d.sync += [
                        self.tx.eq(latch[0]),
                        latch.eq(Cat(latch[1:8], 0)),
                        bitno.eq(bitno + 1),
                    ]
                    with m.If(bitno == 7):
                        m.next = "STOP"
            with m.State("STOP"):
                with m.If(strobe):
                    m.d.sync += self.tx.eq(1)
                    m.next = "IDLE"
        return m
