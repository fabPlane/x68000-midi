"""Simulation rig: a bus master, a slave under test, and a register file.

Shared by the tests and by ``tools/``. The slave is swappable so the same rig
drives either :class:`x68kmidi.bus.BusSlave68k` or the
:class:`x68kmidi.midiori_ref.MidioriBusSlave` reference model, which is what
makes the two comparable rather than merely describable.

``reg_domain`` follows the slave: the reference model runs in its own slow
domain, and its register file has to be clocked there too.
"""

from amaranth import Array, Elaboratable, Module, Mux, Signal

from x68kmidi.bus import MIDI_BASE, BusSlave68k
from x68kmidi.busmaster import BusMaster68k

__all__ = ["Harness", "run_cycle", "slot_addr", "N_REGS"]

N_REGS = 8


def slot_addr(byte_addr: int) -> int:
    """Byte address -> the A1..A23 value the slot carries."""
    return byte_addr >> 1


class Harness(Elaboratable):
    """Master and slave over a shared data bus that reads 0xFF when undriven."""

    def __init__(self, base: int = MIDI_BASE, *, slave=None, reg_domain: str = "sync",
                 out_delay: int = 0, data_delay: int | None = None,
                 dtack_delay: int | None = None):
        """``out_delay`` puts N master-domain flops on the slave's bus outputs,
        standing in for the delay simulation has none of: the level translator,
        the open-drain DTACK buffer, the IOB and the PCB. At a 108 MHz rig that
        is 9.26 ns per flop. Applied equally to either slave, which flatters
        the reference model -- its read data also crosses a wide combinational
        mux that ours does not have.

        ``data_delay`` and ``dtack_delay`` override it per path. The physical
        asymmetry worth testing is data slower than DTACK: midiori drives both
        from the same state, with a wide combinational register mux in the data
        path only, so a CPU can latch stale data while DTACK looked fine --
        exactly what rule 2 (DTACK strictly after the data) exists to prevent.
        """
        self.master = BusMaster68k()
        self.slave = BusSlave68k(base) if slave is None else slave
        self.reg_domain = reg_domain
        self.data_delay = out_delay if data_delay is None else data_delay
        self.dtack_delay = out_delay if dtack_delay is None else dtack_delay
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
        ]

        # The slave's outputs, optionally delayed -- data and DTACK separately.
        sl_data_o, sl_data_oe = sl.data_o, sl.data_oe
        for stage in range(self.data_delay):
            d_o = Signal(8, name=f"dly{stage}_data_o")
            d_oe = Signal(name=f"dly{stage}_data_oe")
            m.d.sync += [d_o.eq(sl_data_o), d_oe.eq(sl_data_oe)]
            sl_data_o, sl_data_oe = d_o, d_oe
        sl_dtack_n = sl.dtack_n
        for stage in range(self.dtack_delay):
            d_dt = Signal(init=1, name=f"dly{stage}_dtack_n")
            m.d.sync += d_dt.eq(sl_dtack_n)
            sl_dtack_n = d_dt
        m.d.comb += ma.dtack_n.eq(sl_dtack_n)

        bus = Signal(8)
        m.d.comb += [
            bus.eq(Mux(ma.data_oe, ma.data_o, Mux(sl_data_oe, sl_data_o, 0xFF))),
            ma.data_i.eq(bus),
            sl.data_i.eq(bus),
            self.contention.eq(ma.data_oe & sl_data_oe),
            sl.rdata.eq(self.regs[sl.reg_addr]),
        ]
        d = m.d[self.reg_domain]
        with m.If(sl.we):
            d += [self.regs[sl.reg_addr].eq(sl.wdata),
                  self.we_count.eq(self.we_count + 1)]
        with m.If(sl.re):
            d += self.re_count.eq(self.re_count + 1)
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
