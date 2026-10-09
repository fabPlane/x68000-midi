"""midiori's bus front end, ported as a reference model.

A deliberately faithful Amaranth transcription of the ``#register io state
machine`` in ``reference/midiori/gateware/midiori.py`` (Migen, CC0), so that
"our bus slave has more margin than midiori's" can be a measured number on the
same rig instead of an argument from reading the source.

Faithful in the four things that set its timing:

* **One slow domain.** midiori clocks everything from a 16 MHz oscillator
  (``UART(..., 16000000, 31250)``, ``default_clk_period = 1e9/16e6``), so the
  slot is sampled with 62.5 ns granularity. This model elaborates into its own
  domain (default ``ref``) which the simulation clocks at 16 MHz while the bus
  master runs far faster.
* **No synchronisers.** ``~AS``, ``~LDS``, ``R/~W``, ``~IACK`` and the address
  go straight into the FSM.
* **DTACK with the data, not after it.** ``_dtready`` and the read-data mux are
  both outputs of the same state, so the data is still crossing a combinational
  decode when the CPU starts counting down to its latch edge.
* **Clocked release.** ``RDATA``/``WWAIT`` are left only when ``~AS`` high is
  seen on a domain edge, so the model can still be driving the bus up to one
  62.5 ns period into the next cycle.

Two simplifications, both of which make this model *kinder* than the real card:

* Zero propagation delay. The real combinational read-data mux, the level
  translators and the open-drain DTACK buffer all add delay that simulation
  does not have.
* No metastability. Sampling an asynchronous ``~AS`` with no synchroniser
  resolves cleanly and deterministically here; on hardware it does not.

So wherever this model fails, the real card fails at least as early. Where it
passes, it may still be marginal.
"""

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from x68kmidi.bus import MIDI_BASE, WINDOW_BYTES

__all__ = ["MidioriBusSlave", "MIDIORI_CLK_HZ"]

MIDIORI_CLK_HZ = 16_000_000


class MidioriBusSlave(wiring.Component):
    """Port-compatible with :class:`x68kmidi.bus.BusSlave68k`, so the same test
    harness can drive either one."""

    def __init__(self, base: int = MIDI_BASE, *, window: int = WINDOW_BYTES,
                 domain: str = "ref"):
        self._base = base
        self._domain = domain
        self._tag_bit = (window // 2).bit_length() - 1
        super().__init__({
            "as_n":    In(1, init=1),
            "lds_n":   In(1, init=1),
            "rw":      In(1, init=1),
            "iack_n":  In(1, init=1),
            "addr":    In(23),
            "data_i":  In(8),
            "data_o":  Out(8),
            "data_oe": Out(1),
            "dtack_n": Out(1, init=1),
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
        tag = self._tag_bit

        hit = Signal()
        m.d.comb += [
            hit.eq(self.addr[tag:] == ((self._base >> 1) >> tag)),
            self.reg_addr.eq(self.addr[:tag]),
            # The read-data mux is combinational on the address, as midiori's
            # `self.data.o.eq(...)` tree is -- no output register anywhere.
            self.wdata.eq(self.data_i),
        ]

        drive = Signal()
        want_vector = Signal()
        ack = Signal()

        with m.FSM(init="IDLE", domain=self._domain):
            with m.State("IDLE"):
                with m.If(~self.iack_n & ~self.irq_n):
                    m.next = "IACK"
                with m.Elif(~self.as_n & hit & ~self.lds_n):
                    with m.If(self.rw):
                        m.next = "RDATA"
                    with m.Else():
                        m.next = "WDATA"

            with m.State("IACK"):
                m.d.comb += [drive.eq(1), want_vector.eq(1), ack.eq(1)]
                with m.If(self.iack_n):
                    m.next = "IDLE"

            with m.State("RDATA"):
                # DTACK and the data in the same state: this is the one that
                # costs margin on a fast machine.
                m.d.comb += [drive.eq(1), ack.eq(1), self.re.eq(1)]
                with m.If(self.as_n):
                    m.next = "IDLE"

            with m.State("WDATA"):
                # Migen's NextValue() samples during this state and commits on
                # the edge leaving it, so the CPU's data must still be on the
                # bus a whole domain period after the cycle was recognised.
                m.d.comb += [ack.eq(1), self.we.eq(1)]
                m.next = "WWAIT"

            with m.State("WWAIT"):
                m.d.comb += ack.eq(1)
                with m.If(self.as_n):
                    m.next = "IDLE"

        # No asynchronous release: the outputs follow the state only.
        m.d.comb += [
            self.data_oe.eq(drive),
            self.dtack_n.eq(~ack),
        ]
        with m.If(want_vector):
            m.d.comb += self.data_o.eq(self.vector)
        with m.Else():
            m.d.comb += self.data_o.eq(self.rdata)
        return m
