"""68000 bus master: the measuring instrument, not part of the card.

This runs on the Tang Console 138K (or in simulation) and generates X68000 slot
cycles with 68000 timing at a *runtime-selectable* speed, so the card's
overclock margin can be characterised on the bench without owning a clocked-up
X68000. It is the answer to "can we beat midiori's timing" being a measurement
rather than a claim.

Timing follows the 68000 read/write cycle in S states (each S state is half a
CPU clock period):

===== ==========================================================
S0    address and R/~W driven
S1    --
S2    ``~AS`` asserted; on a read ``~LDS`` too
S3    on a write, the data is driven
S4    on a write, ``~LDS`` asserted. ``~DTACK`` is sampled at the end of S4
Sw    while ``~DTACK`` is high, wait states are inserted two S states at a time
S5    --
S6    on a read, the data is latched at the end of S6
S7    strobes negate, the data bus is released
===== ==========================================================

``s_len`` is the length of one S state in master clocks, so with the master
domain at *f*, the emulated CPU clock is ``f / (2 * s_len)``:

======== ============== ==============
 s_len    at 108 MHz     at 216 MHz
======== ============== ==============
 5        10.8 MHz       21.6 MHz
 4        13.5 MHz       27 MHz
 3        18 MHz         36 MHz
 2        27 MHz         54 MHz
======== ============== ==============

Run the master domain as fast as the Console will close timing: resolution in
emulated CPU speed is set by ``s_len`` being an integer, so 216 MHz gives twice
as many usable speeds around the interesting 16-25 MHz range.

Reported per cycle: ``dtack_clocks`` (master clocks from ``~AS`` low to
``~DTACK`` low -- the number to compare against midiori), ``wait_states``
inserted, ``timeout`` if the slave never answered, and for reads the byte
latched at the end of S6, which is what a real CPU would have taken.
"""

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import In, Out

__all__ = ["BusMaster68k"]


class BusMaster68k(wiring.Component):
    """Generate one 68000 read, write or interrupt-acknowledge cycle per ``start``."""

    # --- control -------------------------------------------------------
    start: In(1)
    rw_in: In(1, init=1)          # 1 = read
    iack_in: In(1)                # run an interrupt-acknowledge cycle instead
    addr_in: In(23)
    wdata_in: In(8)
    s_len: In(8, init=5)          # master clocks per S state, >= 1
    max_wait: In(16, init=1000)   # wait states before giving up

    # --- status --------------------------------------------------------
    busy: Out(1)
    done: Out(1)                  # one-clock pulse at the end of a cycle
    rdata: Out(8)
    timeout: Out(1)
    dtack_clocks: Out(16)
    wait_states: Out(16)

    # --- slot ----------------------------------------------------------
    as_n: Out(1, init=1)
    lds_n: Out(1, init=1)
    uds_n: Out(1, init=1)
    rw: Out(1, init=1)
    iack_n: Out(1, init=1)
    addr: Out(23)
    data_o: Out(8)
    data_oe: Out(1)
    dtack_n: In(1, init=1)
    data_i: In(8)

    def elaborate(self, platform):
        m = Module()

        dtack_s = Signal(init=1)
        m.submodules.sync_dtack = FFSynchronizer(self.dtack_n, dtack_s, init=1)

        tick = Signal(16)          # master clocks left in this S state
        last = Signal()            # final clock of the S state
        measuring = Signal()
        waits = Signal(16)
        is_iack = Signal()
        is_read = Signal()

        m.d.comb += last.eq(tick == 0)
        with m.If(last):
            m.d.sync += tick.eq(self.s_len - 1)
        with m.Else():
            m.d.sync += tick.eq(tick - 1)

        # Measure ~AS low -> ~DTACK low in master clocks. It stops when the
        # slave answers, not when the CPU gets around to sampling, so the number
        # is the slave's latency and not a function of s_len.
        with m.If(measuring & dtack_s):
            m.d.sync += self.dtack_clocks.eq(self.dtack_clocks + 1)
        with m.If(measuring & ~dtack_s):
            m.d.sync += measuring.eq(0)

        def enter(state):
            """Start an S state with a fresh tick counter."""
            m.d.sync += tick.eq(self.s_len - 1)
            m.next = state

        with m.FSM(init="IDLE"):
            with m.State("IDLE"):
                m.d.sync += [self.as_n.eq(1), self.iack_n.eq(1),
                             self.lds_n.eq(1), self.uds_n.eq(1), self.data_oe.eq(0)]
                with m.If(self.start):
                    m.d.sync += [
                        self.addr.eq(self.addr_in),
                        self.data_o.eq(self.wdata_in),
                        self.rw.eq(self.rw_in | self.iack_in),
                        is_read.eq(self.rw_in | self.iack_in),
                        is_iack.eq(self.iack_in),
                        self.dtack_clocks.eq(0),
                        self.wait_states.eq(0),
                        self.timeout.eq(0),
                        waits.eq(0),
                    ]
                    enter("S0")

            with m.State("S0"):
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    enter("S1")
            with m.State("S1"):
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    # S2: the strobes go down.
                    m.d.sync += measuring.eq(1)
                    with m.If(is_iack):
                        m.d.sync += self.iack_n.eq(0)
                    with m.Else():
                        m.d.sync += self.as_n.eq(0)
                    with m.If(is_read & ~is_iack):
                        m.d.sync += self.lds_n.eq(0)
                    enter("S2")

            with m.State("S2"):
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    # S3: a write puts its data on the bus.
                    with m.If(~is_read):
                        m.d.sync += self.data_oe.eq(1)
                    enter("S3")
            with m.State("S3"):
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    # S4: a write asserts its data strobe.
                    with m.If(~is_read):
                        m.d.sync += self.lds_n.eq(0)
                    enter("S4")

            with m.State("S4"):
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    # End of S4 is where a 68000 samples ~DTACK.
                    with m.If(~dtack_s):
                        enter("S5")
                    with m.Elif(waits >= self.max_wait):
                        # Gave up. Latch the bus anyway so `rdata` always says
                        # what was really there (0xFF if nobody is driving)
                        # rather than leaving the previous cycle's value.
                        m.d.sync += [self.timeout.eq(1), measuring.eq(0),
                                     self.rdata.eq(self.data_i)]
                        enter("S7")
                    with m.Else():
                        m.d.sync += [waits.eq(waits + 1),
                                     self.wait_states.eq(waits + 1)]
                        enter("SW")
            with m.State("SW"):
                # A wait state is two S states long; re-sample at the end.
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    enter("SW2")
            with m.State("SW2"):
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    enter("S4")

            with m.State("S5"):
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    enter("S6")
            with m.State("S6"):
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    # End of S6: the CPU latches read data here, and here only.
                    with m.If(is_read):
                        m.d.sync += self.rdata.eq(self.data_i)
                    m.d.sync += [self.as_n.eq(1), self.iack_n.eq(1),
                                 self.lds_n.eq(1), self.data_oe.eq(0)]
                    enter("S7")
            with m.State("S7"):
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    enter("RECOVER")
            with m.State("RECOVER"):
                # One idle bus clock before the next cycle may start.
                m.d.comb += self.busy.eq(1)
                with m.If(last):
                    m.d.comb += self.done.eq(1)
                    m.next = "IDLE"
        return m
