"""68000 asynchronous bus slave for the X68000 I/O slot.

Re-architected from midiori's ``#register io state machine`` (Migen, CC0) after
reading why that design loses on clocked-up machines; see
``docs/DECISIONS.md`` 2026-10-09. The six rules this implements:

1. **Fast sample domain.** Everything here is meant to run at ~108 MHz (4 x the
   27 MHz oscillator), not at the MIDI bit clock. midiori samples the slot at
   16 MHz, i.e. with 62.5 ns granularity, and a 25 MHz 68030 cycle holds ``~AS``
   low for only ~80 ns -- so it can miss a cycle outright.
2. **Honest ``~DTACK``.** Asserted a clock *after* the read data is registered
   and the output enable is on, never in the same cycle as a decode. Late DTACK
   only costs the CPU a wait state; early DTACK corrupts the transfer.
3. **Registered read data.** ``rdata`` is captured continuously while idle (the
   address is stable long before the strobes), so at access time the path is
   FF -> IOB -> level translator with no combinational decode in it.
4. **Asynchronous release.** ``data_oe`` and ``dtack_n`` are gated
   combinationally by the *raw* ``as_n``/``iack_n`` pins, so the card is off the
   bus as soon as the strobes negate, at any CPU speed. midiori leaves the FSM
   in its drive state until a 16 MHz edge sees ``~AS`` high, which can collide
   with the next cycle's driver.
5. **Synchronisers** on every asynchronous control input (2 FF = 18.5 ns at
   108 MHz).
6. Being slow is allowed; being early is not. A PSRAM read that needs wait
   states is correct, as long as DTACK waits with it.

Address lines are A1..A23 as they appear on the slot (``addr[0]`` is A1), which
is also how midiori numbers them. The default window is the 16 bytes at
``$EAFA00`` that every X68000 MIDI driver talks to; a second card instance at
``$EAFA10`` just takes a different ``base``.
"""

from amaranth import Module, Signal, Mux
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import In, Out

__all__ = ["BusSlave68k", "MIDI_BASE"]

MIDI_BASE = 0xEAFA00
WINDOW_BYTES = 16


class BusSlave68k(wiring.Component):
    """Decode one 16-byte window on the slot and present a register port.

    Slot side signals are named as they are on the connector: ``_n`` suffixes are
    active low, and ``rw`` is high for a read. ``dtack_n`` is the value fed to the
    open-drain buffer that pulls the shared ``~DTACK`` line, so 0 means asserted.

    Device side: ``rdata`` must be a function of ``reg_addr`` alone and stable
    whenever the address is -- it is sampled before the cycle commits. ``re`` and
    ``we`` are one-clock strobes for registers with side effects; ``wdata`` is
    valid while ``we`` is high.
    """

    def __init__(self, base: int = MIDI_BASE, *, window: int = WINDOW_BYTES):
        if base % window:
            raise ValueError(f"base {base:#x} is not aligned to a {window}-byte window")
        if window not in (8, 16, 32):
            raise ValueError("window must be 8, 16 or 32 bytes")
        self._base = base
        self._window = window
        # addr[0] is A1, so a `window`-byte window spans window//2 addr steps.
        self._tag_bit = (window // 2).bit_length() - 1
        super().__init__({
            # --- slot, after the level translators -------------------------
            "as_n":    In(1, init=1),
            "lds_n":   In(1, init=1),
            "rw":      In(1, init=1),
            "iack_n":  In(1, init=1),
            "addr":    In(23),
            "data_i":  In(8),
            "data_o":  Out(8),
            "data_oe": Out(1),
            "dtack_n": Out(1, init=1),
            # --- device ----------------------------------------------------
            "irq_n":    In(1, init=1),
            "vector":   In(8),
            "reg_addr": Out(self._tag_bit),
            "rdata":    In(8),
            "wdata":    Out(8),
            "re":       Out(1),
            "we":       Out(1),
        })

    @property
    def base(self) -> int:
        return self._base

    def elaborate(self, platform):
        m = Module()

        # Rule 5: the strobes are asynchronous to our clock.
        as_s, lds_s, rw_s, iack_s = (Signal(init=1) for _ in range(4))
        m.submodules.sync_as = FFSynchronizer(self.as_n, as_s, init=1)
        m.submodules.sync_lds = FFSynchronizer(self.lds_n, lds_s, init=1)
        m.submodules.sync_rw = FFSynchronizer(self.rw, rw_s, init=1)
        m.submodules.sync_iack = FFSynchronizer(self.iack_n, iack_s, init=1)

        # The address is valid before ~AS falls and stays valid for the whole
        # cycle, so by the time the synchronisers have let us look at it (2+
        # clocks after the edge) it has been stable far longer than any
        # metastability window. Compare it combinationally.
        tag = self._tag_bit
        hit = Signal()
        m.d.comb += hit.eq(self.addr[tag:] == ((self._base >> 1) >> tag))
        m.d.comb += self.reg_addr.eq(self.addr[:tag])

        busy = Signal()
        drive = Signal()
        want_vector = Signal()
        ack = Signal()

        # Rule 3: track the device's read data and the vector while idle, so a
        # cycle never waits on a decode. Frozen once a cycle commits, in case a
        # read has side effects on the value it is returning.
        rdata_r = Signal(8)
        vector_r = Signal(8)
        wdata_r = Signal(8)
        with m.If(~busy):
            m.d.sync += [
                rdata_r.eq(self.rdata),
                vector_r.eq(self.vector),
                # Latched on the same edge that commits a write cycle, i.e.
                # while the CPU still has the data on the bus.
                wdata_r.eq(self.data_i),
            ]

        m.d.comb += [
            self.data_o.eq(Mux(want_vector, vector_r, rdata_r)),
            self.wdata.eq(wdata_r),
        ]

        with m.FSM(init="IDLE"):
            with m.State("IDLE"):
                with m.If(~iack_s & ~self.irq_n):
                    m.next = "IACK"
                with m.Elif(~as_s & ~lds_s & hit):
                    with m.If(rw_s):
                        m.next = "READ"
                    with m.Else():
                        m.next = "WRITE"

            # One clock with the data driven but DTACK still negated: rule 2.
            with m.State("READ"):
                m.d.comb += [busy.eq(1), drive.eq(1), self.re.eq(1)]
                m.next = "READ_ACK"
            with m.State("READ_ACK"):
                m.d.comb += [busy.eq(1), drive.eq(1), ack.eq(1)]
                with m.If(as_s):
                    m.next = "IDLE"

            with m.State("WRITE"):
                m.d.comb += [busy.eq(1), self.we.eq(1), ack.eq(1)]
                m.next = "WRITE_HOLD"
            with m.State("WRITE_HOLD"):
                m.d.comb += [busy.eq(1), ack.eq(1)]
                with m.If(as_s):
                    m.next = "IDLE"

            with m.State("IACK"):
                m.d.comb += [busy.eq(1), drive.eq(1), want_vector.eq(1)]
                m.next = "IACK_ACK"
            with m.State("IACK_ACK"):
                m.d.comb += [busy.eq(1), drive.eq(1), want_vector.eq(1), ack.eq(1)]
                with m.If(iack_s):
                    m.next = "IDLE"

        # Rule 4: off the bus the moment the strobes negate, whatever the FSM
        # still thinks. Deliberately combinational on the raw pins.
        released = Signal()
        m.d.comb += [
            released.eq(self.as_n & self.iack_n),
            self.data_oe.eq(drive & ~released),
            self.dtack_n.eq(~(ack & ~released)),
        ]
        return m
