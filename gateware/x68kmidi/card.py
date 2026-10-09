"""The card: two MIDI ports on the X68000 slot.

Instance 0 answers at ``$EAFA00`` on ``~IRQ2`` and is the one every game and
driver expects. Instance 1 answers at ``$EAFA10`` on ``~IRQ4`` and feeds the
second OUT jack, for the sequencer software that drives two boards for 32
channels; nothing is harmed if its jack is never fitted.

One 108 MHz domain throughout (see :mod:`x68kmidi.ym3802`). The 8 MB RAM window
is a third decoder on the same bus and lands here when its PSRAM controller
does; the data bus, ``~DTACK`` wired-OR and translator direction below are
already shaped for another participant.
"""

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from x68kmidi.bus import BusSlave68k
from x68kmidi.ym3802 import CARD_CLK_HZ, Ym3802

__all__ = ["Card", "PORT_BASES"]

PORT_BASES = (0xEAFA00, 0xEAFA10)


class Card(wiring.Component):
    """Slot pins in, MIDI out. Everything below the level translators."""

    # --- slot, after the translators ----------------------------------
    as_n: In(1, init=1)
    lds_n: In(1, init=1)
    rw: In(1, init=1)
    iack2_n: In(1, init=1)
    iack4_n: In(1, init=1)
    addr: In(23)
    data_i: In(8)
    data_o: Out(8)
    data_oe: Out(1)
    dtack_n: Out(1, init=1)
    irq2_n: Out(1, init=1)
    irq4_n: Out(1, init=1)
    exreset_n: In(1, init=1)

    # --- panel ---------------------------------------------------------
    midi_tx: Out(1, init=1)
    midi_tx2: Out(1, init=1)

    def __init__(self, clk_freq: int = CARD_CLK_HZ, *, bases=PORT_BASES, **kw):
        self._clk_freq = clk_freq
        self._bases = bases
        self._ym_kw = kw
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        slaves, cores = [], []
        for n, base in enumerate(self._bases):
            slave = BusSlave68k(base)
            core = Ym3802(self._clk_freq, **self._ym_kw)
            m.submodules[f"slave{n}"] = slave
            m.submodules[f"ym{n}"] = core
            m.d.comb += [
                slave.as_n.eq(self.as_n),
                slave.lds_n.eq(self.lds_n),
                slave.rw.eq(self.rw),
                slave.addr.eq(self.addr),
                slave.data_i.eq(self.data_i),
                slave.iack_n.eq(self.iack2_n if n == 0 else self.iack4_n),
                slave.irq_n.eq(core.irq_n),
                slave.vector.eq(core.vector),
                core.reg_addr.eq(slave.reg_addr),
                core.wdata.eq(slave.wdata),
                core.we.eq(slave.we),
                core.re.eq(slave.re),
                slave.rdata.eq(core.rdata),
            ]
            slaves.append(slave)
            cores.append(core)

        # Only one window can decode at a time, so the data bus is a plain OR
        # of whoever is driving. ~DTACK and the IRQs are open drain on the slot,
        # so a 1 here means released and the AND is the wired-OR.
        data_o = Signal(8)
        data_oe = Signal()
        dtack_n = Signal(init=1)
        for slave in slaves:
            with m.If(slave.data_oe):
                m.d.comb += data_o.eq(slave.data_o)
        m.d.comb += [
            data_oe.eq(slaves[0].data_oe | slaves[1].data_oe),
            dtack_n.eq(slaves[0].dtack_n & slaves[1].dtack_n),
            self.data_o.eq(data_o),
            self.data_oe.eq(data_oe),
            self.dtack_n.eq(dtack_n),
            self.irq2_n.eq(cores[0].irq_n),
            self.irq4_n.eq(cores[1].irq_n),
            self.midi_tx.eq(cores[0].tx),
            self.midi_tx2.eq(cores[1].tx),
        ]
        return m
