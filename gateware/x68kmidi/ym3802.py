"""YM3802 "MCS" MIDI controller, transmit side — the registers games talk to.

Ported from midiori's ``Midiori`` class (Migen, CC0) with the same register map
and the same observable behaviour, because that map is the entire compatibility
contract: every X68000 MIDI driver and game pokes a YM3802 at ``$EAFA00`` and
nothing else. Where midiori returns a constant for a register it never
implemented, so do we — those constants are what the drivers probe for.

Addressing: ``reg_addr`` is A1..A3, selecting one of eight byte registers in the
16-byte window. Register 1 writes the *group* number, and most registers are
then addressed as ``group << 4 | reg_addr`` — so the eight-register window is
banked sixteen ways. Registers 0..3 are outside the banking:

===== ========================= =================================
 A1-3  read                      write
===== ========================= =================================
 0     interrupt vector (IVR)    --
 1     --                        group number, bit 7 = reset
 2     interrupt status (ISR)    --
 3     --                        clear the ISR bits that are set
===== ========================= =================================

Banked registers implemented, by ``group<<4 | reg``:

======= ========================================================
 0x04    write: interrupt vector offset (IVO), bits 5-7
 0x05    write: interrupt mode register (IMR), bits 0-3
 0x06    write: interrupt enable (IER)
 0x14    write: active sensing enable, bit 5
 0x34    read: 0x04 constant
 0x36    read: 0x00 constant
 0x54    read: transmit status (TSR)
 0x55    write: TXE bit 0, clear TXIDL bit 2, BRKE bit 3
 0x56    write: byte into the transmit FIFO
 0x64    read: 0xA0 constant
 0x66    write: CLKM, bit 1 — 4 us or 8 us timer tick
 0x67    write: click counter reload, bit 7 loads it now
 0x74    read: 0x00 constant
 0x84/85 write: general purpose timer reload, low then high
 0x86/87 write: MIDI clock timer reload, low then high
 0x96    read: 0xFF constant
 0xF4    write: version string index
 0xF5    read: version string byte at that index
======= ========================================================

Deliberately not implemented, exactly as midiori: receive (FIFO-Rx, FIFO-IRx),
the FIFO-ITx start/stop/clock messages, SYNC, CLICK output, the sequencer and
GPIO. The one card feature that exercises them is a sequencer recording from a
keyboard; no game needs any of it.

The whole card is one 108 MHz clock domain: 108 MHz / 31250 = 3456 and
108 MHz x 8 us = 864, both exact, so neither the MIDI bit rate nor the timer
tick needs a second clock or a fractional divider.
"""

from amaranth import Array, Cat, Const, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.fifo import SyncFIFOBuffered
from amaranth.lib.wiring import In, Out

from x68kmidi.uart import UartTx

__all__ = ["Ym3802", "VERSION_STRING", "CARD_CLK_HZ"]

CARD_CLK_HZ = 108_000_000
VERSION_STRING = b"x68000-midi 0.1 (midiori-compatible)"
ACTIVE_SENSING = 0xFE
#: midiori waits 1,280,000 cycles of its 16 MHz clock before deciding the
#: transmitter is idle, i.e. 80 ms — the MIDI active-sensing interval.
IDLE_SECONDS = 0.08


class Ym3802(wiring.Component):
    """Register file and transmitter. Pair with :class:`x68kmidi.bus.BusSlave68k`."""

    reg_addr: In(3)
    rdata: Out(8)
    wdata: In(8)
    re: In(1)                  # unused: no register here has a read side effect
    we: In(1)
    irq_n: Out(1, init=1)
    vector: Out(8)
    tx: Out(1, init=1)

    def __init__(self, clk_freq: int = CARD_CLK_HZ, *, idle_ticks: int | None = None,
                 tick_divisor: int | None = None):
        self._clk_freq = clk_freq
        # 8 us (CLKM=1) and 4 us (CLKM=0) timer ticks.
        self._tick = tick_divisor if tick_divisor is not None \
            else int(round(clk_freq * 4e-6))
        self._idle = idle_ticks if idle_ticks is not None \
            else int(round(clk_freq * IDLE_SECONDS))
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        version = list(VERSION_STRING) + [0]

        m.submodules.uart = uart = UartTx(self._clk_freq)
        m.submodules.fifo = fifo = SyncFIFOBuffered(width=8, depth=16)

        # --- transmit path ------------------------------------------------
        txe = Signal(init=1)      # transmitter enable
        brke = Signal()           # send a break (hold the line low)
        ase = Signal()            # active-sensing enable
        itx_fe = Signal()         # an active-sensing byte is pending
        itx_busy = Signal()

        m.d.comb += [
            uart.tx_ready.eq(fifo.r_rdy | itx_fe),
            self.tx.eq(~brke & uart.tx),
        ]
        with m.If(itx_fe):
            m.d.comb += uart.tx_data.eq(ACTIVE_SENSING)
        with m.Else():
            m.d.comb += uart.tx_data.eq(fifo.r_data)
        with m.If(~itx_busy):
            m.d.comb += fifo.r_en.eq(uart.tx_ack & ~itx_fe)
        # The "4-deep ITx FIFO" in the datasheet is really four kinds of
        # message, so one in flight at a time is the whole of it.
        with m.If(uart.tx_ack & itx_fe & ~itx_busy):
            m.d.sync += [itx_fe.eq(0), itx_busy.eq(1)]
        with m.Elif(uart.tx_ack & itx_busy):
            m.d.sync += itx_busy.eq(0)

        # --- transmit status ----------------------------------------------
        txemp = Signal()
        txrdy = Signal()
        txidl = Signal()
        txbsy = Signal()
        m.d.comb += [txemp.eq(fifo.level == 0), txrdy.eq(fifo.w_rdy), txbsy.eq(0)]

        idle_counter = Signal(range(self._idle + 1))
        with m.If(idle_counter >= self._idle):
            m.d.sync += [idle_counter.eq(0), txidl.eq(1), itx_fe.eq(ase)]
        with m.Elif(~txemp | ~txe):
            m.d.sync += idle_counter.eq(0)
        with m.Else():
            m.d.sync += idle_counter.eq(idle_counter + 1)

        tsr = Signal(8)
        m.d.comb += tsr.eq(Cat(txbsy, 0, txidl, 0, 0, 0, txrdy, txemp))

        # --- read/write registers ------------------------------------------
        group = Signal(4)
        ier = Signal(8)
        ivo = Signal(3)
        ic = Signal()
        imr = Signal(4)
        clkm = Signal()
        version_index = Signal(range(len(version)))
        # The version string as a mux tree, not a chain of comparators.
        #
        # This used to be `for i, byte in enumerate(version): with m.If(
        # version_index == i)`, which is 38 equality tests in a priority chain
        # feeding `rdata` combinationally -- and `rdata` feeds the bus slave's
        # registered read data. nextpnr put the critical path right through it:
        # 9.52 ns from ym1.version_index to slave1.rdata_r, for a measured
        # ceiling of 105.02 MHz against the 108 MHz this card runs at.
        #
        # An Array indexed by a signal elaborates to a balanced mux instead,
        # which is log-depth rather than linear. Registering it as well costs a
        # cycle of latency that nothing can observe: `rdata` is sampled
        # continuously while the slave is idle, and the 68000 holds an address
        # valid for tens of nanoseconds before ~AS, which the slave then takes
        # two more cycles to synchronise.
        version_byte = Signal(8)
        m.d.sync += version_byte.eq(Array([Const(b, 8) for b in version])
                                    [version_index])
        reg_num = Signal(8)
        m.d.comb += reg_num.eq(Cat(self.reg_addr, 0, group))

        # --- interrupts -------------------------------------------------
        isr = Signal(8)
        masked = Signal(8)
        enc_o = Signal(3)
        enc_n = Signal(init=1)
        m.d.comb += masked.eq(isr & ier)
        for i in reversed(range(8)):        # lowest set bit wins
            with m.If(masked[i]):
                m.d.comb += [enc_o.eq(i), enc_n.eq(0)]
        m.d.comb += [
            self.vector.eq(Cat(0, enc_o, enc_n, ivo)),
            self.irq_n.eq(masked == 0),
        ]

        prev_empty = Signal()
        m.d.sync += prev_empty.eq(txemp)
        with m.If(~prev_empty & txemp):
            m.d.sync += isr[6].eq(1)
        with m.Elif(prev_empty & ~txemp):
            m.d.sync += isr[6].eq(0)

        # --- timers ----------------------------------------------------
        tick_div = Signal(range(2 * self._tick))
        tick = Signal()
        m.d.sync += tick_div.eq(tick_div - 1)
        with m.If(clkm):
            m.d.comb += tick.eq(tick_div == 0)
        with m.Else():
            m.d.comb += tick.eq(tick_div[:len(tick_div) - 1] == 0)

        gpt_low = Signal(8)
        gpt_reload = Signal(14)
        gpt_count = Signal(14)
        with m.If(tick):
            with m.If((gpt_count == 0) & (gpt_reload > 1)):
                m.d.sync += [gpt_count.eq(gpt_reload), isr[7].eq(1)]
            with m.Else():
                m.d.sync += gpt_count.eq(gpt_count - 1)

        clk_low = Signal(8)
        clk_reload = Signal(14)
        clk_count = Signal(14)
        click_count = Signal(7)
        click_reload = Signal(7)
        with m.If(tick):
            with m.If((clk_count == 0) & (clk_reload > 1)):
                m.d.sync += clk_count.eq(clk_reload)
                with m.If(imr[3]):
                    m.d.sync += isr[1].eq(1)
            with m.Else():
                m.d.sync += clk_count.eq(clk_count - 1)
        with m.If(tick & (clk_count == 0)):
            with m.If((click_count == 1) & (click_reload > 0)):
                m.d.sync += click_count.eq(click_reload)
                with m.If(~imr[3]):
                    m.d.sync += isr[1].eq(1)
            with m.Else():
                m.d.sync += click_count.eq(click_count - 1)

        # --- reads ------------------------------------------------------
        with m.Switch(self.reg_addr):
            with m.Case(0):
                m.d.comb += self.rdata.eq(self.vector)
            with m.Case(2):
                m.d.comb += self.rdata.eq(isr)
            with m.Default():
                with m.Switch(reg_num):
                    with m.Case(0x34):
                        m.d.comb += self.rdata.eq(0x04)
                    with m.Case(0x54):
                        m.d.comb += self.rdata.eq(tsr)
                    with m.Case(0x64):
                        m.d.comb += self.rdata.eq(0xA0)
                    with m.Case(0x96):
                        m.d.comb += self.rdata.eq(0xFF)
                    with m.Case(0xF5):
                        m.d.comb += self.rdata.eq(version_byte)
                    # 0x36 and 0x74 read as 0x00, like every unmapped register.

        # --- writes -----------------------------------------------------
        m.d.sync += fifo.w_en.eq(0)
        with m.If(self.we):
            with m.Switch(self.reg_addr):
                with m.Case(1):
                    m.d.sync += [group.eq(self.wdata[:4]), ic.eq(self.wdata[7])]
                with m.Case(3):
                    m.d.sync += isr.eq(isr & ~self.wdata)
                with m.Default():
                    with m.Switch(reg_num):
                        with m.Case(0x04):
                            m.d.sync += ivo.eq(self.wdata[5:8])
                        with m.Case(0x05):
                            m.d.sync += imr.eq(self.wdata[:4])
                        with m.Case(0x06):
                            m.d.sync += ier.eq(self.wdata)
                        with m.Case(0x14):
                            m.d.sync += ase.eq(self.wdata[5])
                        with m.Case(0x55):
                            m.d.sync += [txe.eq(self.wdata[0]), brke.eq(self.wdata[3])]
                            with m.If(self.wdata[2]):
                                m.d.sync += txidl.eq(0)
                        with m.Case(0x56):
                            m.d.sync += [fifo.w_en.eq(1), fifo.w_data.eq(self.wdata),
                                         isr[6].eq(0)]
                        with m.Case(0x66):
                            m.d.sync += clkm.eq(self.wdata[1])
                        with m.Case(0x67):
                            m.d.sync += click_reload.eq(self.wdata[:7])
                            with m.If(self.wdata[7]):
                                m.d.sync += click_count.eq(self.wdata[:7])
                        with m.Case(0x84):
                            m.d.sync += gpt_low.eq(self.wdata)
                        with m.Case(0x85):
                            m.d.sync += gpt_reload.eq(Cat(gpt_low, self.wdata[:6]))
                            with m.If(self.wdata[7]):
                                m.d.sync += gpt_count.eq(Cat(gpt_low, self.wdata[:6]))
                        with m.Case(0x86):
                            m.d.sync += clk_low.eq(self.wdata)
                        with m.Case(0x87):
                            m.d.sync += clk_reload.eq(Cat(clk_low, self.wdata[:6]))
                            with m.If(self.wdata[7]):
                                m.d.sync += clk_count.eq(Cat(clk_low, self.wdata[:6]))
                        with m.Case(0xF4):
                            with m.If(self.wdata < len(version)):
                                m.d.sync += version_index.eq(self.wdata)

        with m.If(ic):
            m.d.sync += [group.eq(0), clkm.eq(0), ivo.eq(0), ic.eq(0)]
        return m
