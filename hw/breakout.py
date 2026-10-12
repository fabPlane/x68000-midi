"""X68000 slot breakout — the whole expansion bus on two ribbon cables.

    python3 hw/breakout.py          # -> build/x68000-breakout.net, report

A dumb, universal card: the 2 x 50 edge fingers, six level translators, and two
2 x 20 IDC headers. Plug an FPGA or MCU board on the other end and it is an
X68000 expansion card you can re-flash in seconds. Nothing on it is specific to
the MIDI project.

Why build it before the real card:

* it proves the three mechanical unknowns cheaply — the **edge-finger
  footprint**, the **45 degree bevel** (midiori's fab would not do it), and the
  plate and card-guide fit;
* it puts the Tang Console on a real machine, which is the only way to finish
  the bus-slave bring-up and to measure overclock margin against hardware
  rather than against the simulated rig;
* every later card — RAM, NE2000 Ethernet, Mercury-style PCM, a SCSI board, a
  bus analyser — prototypes on the same board.

Two build options, one PCB:

* **buffered** (default): six SN74LVC8T245 with their B side at whatever the
  host board supplies on VREF, so levels always match. This is what the Tang
  Console needs — Sipeed warn that 5 V on its headers destroys the board.
* **passive**: leave the translators unpopulated and fit the bypass links, for
  a 5 V-tolerant host such as an RP2350.

Grouping follows the ribbons, not the connector: J2 carries everything a cycle
needs (data, strobes, DTACK, interrupts) and J3 carries the address bus, so a
card that never does DMA can ignore the spare lines on J3.

**The headers are right-angle.** The cable leaves in the plane of the card,
toward the rear, rather than straight up into the neighbouring slot — which
matters on the bench, and matters more if the other end is not a dev board but
a second PCB sitting behind this one in the same slot. That is the shape a
production two-board set would take: this card carries the fingers and the
translators, a logic card behind it carries the FPGA, its memory and the MIDI
drivers, and a short ribbon joins them. Revising the logic half then costs one
small board instead of a 150 mm one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from netlist import (SLOT, SLOT_NC, Design, check, emit_netlist,  # noqa: E402
                     renumber)

ROOT = Path(__file__).resolve().parent.parent

#: Slot nets that are not in the MIDI card's SLOT map but which a general
#: breakout should still carry: the bus-master group and the 10 MHz clock.
EXTRA_SLOT = {
    "B2": "CLK10M", "B36": "EXREQ_N", "B37": "EXACK_N",
    "B46": "BR_N", "B47": "BG_N", "B48": "BGACK_N",
    "B40": "EXNMI_N", "A39": "EXBERR_N",
}

#: Signals per header. A ground every fifth pin plus the two at the far end,
#: which at 10 MHz over a short ribbon is enough; a card that cares can run a
#: second ribbon of ground instead.
J2_SIGNALS = (
    [f"SD{i}" for i in range(16)]
    + ["AS_N", "UDS_N", "LDS_N", "RW", "DTACK_N", "IRQ2_N", "IRQ4_N",
       "IACK2_N", "IACK4_N", "IDDIR", "EXRESET_N", "BUS_DIR"]
)
J3_SIGNALS = (
    [f"SA{i}" for i in range(1, 24)]
    + ["CLK10M", "EXREQ_N", "EXACK_N", "BR_N", "BG_N"]
)


def header(d: Design, ref: str, signals: list[str], host_side: str) -> None:
    """Lay a signal list into a 2x20 IDC with grounds and the supply pins.

    Pin 1 is VREF (the host's I/O voltage, which sets the translators' B side),
    pin 2 is +5 V from the slot behind a fuse, pin 39/40 are ground, and every
    fourth pin in between is ground.
    """
    d.add(ref, "IDC40", f"2x20 IDC right-angle -> {host_side}")
    d.wire("VREF", ref, "p1")
    d.wire("+5V_FUSED", ref, "p2")
    pins = [n for n in range(3, 39)]
    grounds = [n for n in pins if (n - 3) % 5 == 4]
    slots = [n for n in pins if n not in grounds]
    if len(signals) > len(slots):
        raise SystemExit(f"{ref}: {len(signals)} signals into {len(slots)} pins")
    for n in grounds + [39, 40]:
        d.wire("GND", ref, f"p{n}")
    for n, net in zip(slots, signals):
        d.wire(net, ref, f"p{n}")
    for n in slots[len(signals):]:
        d.wire("GND", ref, f"p{n}")     # unused pins become more ground


def translator(d: Design, ref: str, a_nets, b_nets, *, dir_net, populated=True):
    d.add(ref, "LVC8T245", "SN74LVC8T245", dnp=not populated)
    d.wire("+5V", ref, "V_{CCA}")
    d.wire("VREF", ref, "V_{CCB}")
    d.wire("GND", ref, "GND")
    d.wire(dir_net, ref, "DIR")
    d.wire("GND", ref, "~{OE}")
    for i, (a, b) in enumerate(zip(a_nets, b_nets), start=1):
        if a is None:
            continue
        d.wire(a, ref, f"A{i}")
        d.wire(b, ref, f"B{i}")


def build() -> Design:
    d = Design()

    slot = dict(SLOT)
    slot.update(EXTRA_SLOT)
    edge_pins = {f"{s}{n}": [f"{s}{n}"] for s in "AB" for n in range(1, 51)}
    d.add("J1", "EDGE", "X68000 I/O slot", pins=edge_pins)
    for pad, net in slot.items():
        d.wire(net, "J1", pad)

    # --- direction control ------------------------------------------------
    # DIR and ~OE on an 8T245 are referenced to V_CCA, which is the 5 V slot,
    # so the host's 3.3 V cannot drive them directly. One 74AHCT1G34 (TTL
    # thresholds at 5 V) translates the host's BUS_DIR; everything else only
    # ever flows slot -> host, so its DIR is strapped.
    d.add("U7", "LVC1G07", "74AHCT1G34")
    d.wire("+5V", "U7", "VCC")
    d.wire("GND", "U7", "GND")
    d.wire("BUS_DIR", "U7", "A")
    d.wire("BUS_DIR_5V", "U7", "Y")

    # --- translators -------------------------------------------------------
    # Host-side nets keep the slot's names: this board renames nothing, so a
    # logic analyser on the ribbon reads the same signal names as the manual.
    translator(d, "U1", [f"SD{i}" for i in range(8)],
               [f"HD{i}" for i in range(8)], dir_net="BUS_DIR_5V")
    translator(d, "U2", [f"SD{i}" for i in range(8, 16)],
               [f"HD{i}" for i in range(8, 16)], dir_net="BUS_DIR_5V")
    translator(d, "U3", [f"SA{i}" for i in range(1, 9)],
               [f"HA{i}" for i in range(1, 9)], dir_net="+5V")
    translator(d, "U4", [f"SA{i}" for i in range(9, 17)],
               [f"HA{i}" for i in range(9, 17)], dir_net="+5V")
    translator(d, "U5", [f"SA{i}" for i in range(17, 24)] + ["CLK10M"],
               [f"HA{i}" for i in range(17, 24)] + ["HCLK10M"], dir_net="+5V")
    ctrl_in = ["AS_N", "UDS_N", "LDS_N", "RW", "IACK2_N", "IACK4_N",
               "EXRESET_N", "IDDIR"]
    translator(d, "U6", ctrl_in, [f"H{n}" for n in ctrl_in], dir_net="+5V")

    # Outputs the card drives onto the slot are open drain, as the bus is
    # wired-OR: the motherboard provides the pull-ups.
    d.add("U8", "LVC2G07", "74LVC2G07")
    d.add("U9", "LVC2G07", "74LVC2G07")
    for ref in ("U8", "U9"):
        d.wire("VREF", ref, "VCC")
        d.wire("GND", ref, "GND")
    for ref, (ia, oa, ib, ob) in {
        "U8": ("HDTACK_N", "DTACK_N", "HIRQ2_N", "IRQ2_N"),
        "U9": ("HIRQ4_N", "IRQ4_N", "HEXREQ_N", "EXREQ_N"),
    }.items():
        d.wire(ia, ref, "1A")
        d.wire(oa, ref, "1Y")
        d.wire(ib, ref, "2A")
        d.wire(ob, ref, "2Y")

    # --- headers ------------------------------------------------------------
    # The host sees its own names: H* on the ribbon, S* on the fingers.
    host = {n: f"H{n}" for n in J2_SIGNALS + J3_SIGNALS}
    host.update({f"SD{i}": f"HD{i}" for i in range(16)})
    host.update({f"SA{i}": f"HA{i}" for i in range(1, 24)})
    host["BUS_DIR"] = "BUS_DIR"
    host["CLK10M"] = "HCLK10M"
    for n in ("EXACK_N", "BR_N", "BG_N"):
        host[n] = n                     # straight through, 5 V -- see notes

    header(d, "J2", [host[n] for n in J2_SIGNALS], "host: cycle + data")
    header(d, "J3", [host[n] for n in J3_SIGNALS], "host: address + spare")

    # --- power --------------------------------------------------------------
    # +5 V comes off the slot for the translators' A side. The host supplies
    # VREF, so the B side is whatever it runs at and no regulator is needed;
    # JP1 bridges a local 3.3 V LDO in for a host that cannot source VREF.
    d.add("U10", "LDO", "AP2112K-3.3", dnp=True)
    d.wire("+5V", "U10", "VIN")
    d.wire("+5V", "U10", "EN")
    d.wire("GND", "U10", "GND")
    d.wire("VREF_LOCAL", "U10", "VOUT")
    d.add("JP1", "R", "0R link: local 3.3 V -> VREF", dnp=True)
    d.wire("VREF_LOCAL", "JP1", "1")
    d.wire("VREF", "JP1", "2")

    d.add("F1", "R", "polyfuse 500mA")
    d.wire("+5V", "F1", "1")
    d.wire("+5V_FUSED", "F1", "2")

    for i, (net, value) in enumerate([("+5V", "10uF"), ("VREF", "10uF")]):
        d.add(f"CB{i}", "C", value)
        d.wire(net, f"CB{i}", "1")
        d.wire("GND", f"CB{i}", "2")
    d.decouple("CA", 7, "+5V")          # one per translator A side + the AHCT
    d.decouple("CV", 8, "VREF")         # B sides + the two '07s

    # The three bus signals with nowhere else to go get a pad to scope: a
    # breakout is also the only convenient place to watch the machine misbehave.
    for net in ("+5V", "VREF", "GND", "DTACK_N", "AS_N",
                "BGACK_N", "EXBERR_N", "EXNMI_N"):
        ref = f"TP{net.strip('+')}"
        d.add(ref, "TP", net)
        d.wire(net, ref, "1")

    d.notes += [
        "VREF comes from the host on pin 1 of both headers and sets the "
        "translators' B side, so the breakout matches whatever the host runs "
        "at. The Tang Console is 3.3 V and Sipeed warn that 5 V on its headers "
        "destroys the board -- never fit JP1 and feed VREF at the same time.",
        "Passive build: leave U1-U6 unpopulated and bridge each A/B pair, for a "
        "5 V-tolerant host such as an RP2350. The '07 outputs and the AHCT stay.",
        "~EXACK, ~BR and ~BG go to the header unbuffered at 5 V -- they are "
        "there for a future bus-master experiment, and a 3.3 V host must not "
        "drive or sample them without its own translator.",
        "Keep the ribbons short. At 10 MHz asynchronous the latency does not "
        "matter but the ringing does; 10-15 cm with a ground every fifth pin "
        "is the design point.",
        "~BGACK, ~EXBERR and ~EXNMI are brought to test pads only -- nothing on "
        "this board drives them, but they are the three worth a scope probe "
        "when a card hangs the machine.",
    ]
    return d


def report(d: Design, bad: list[str]) -> str:
    lines = ["# Slot breakout review", "",
             f"{len(d.comps)} components, {len(d.nets)} nets.", ""]
    dnp = sorted(r for r, c in d.comps.items() if c["dnp"])
    lines.append(f"Not populated by default: {', '.join(dnp)}.")
    lines.append("")
    lines.append("## Headers")
    for ref, sigs in (("J2", J2_SIGNALS), ("J3", J3_SIGNALS)):
        lines.append(f"* {ref}: {len(sigs)} signals — "
                     + ", ".join(sigs[:6]) + ", …")
    lines.append("")
    lines.append("## Notes")
    lines += [f"* {n}" for n in d.notes]
    lines.append("")
    lines.append("## Checks")
    if bad:
        lines.append(f"**{len(bad)} problem(s):**")
        lines += [f"* {b}" for b in bad]
    else:
        lines.append("All checks pass.")
    return "\n".join(lines) + "\n"


def main() -> int:
    d = build()
    renumber(d)
    bad = check(d)
    out = ROOT / "build"
    out.mkdir(exist_ok=True)
    (out / "x68000-breakout.net").write_text(emit_netlist(d))
    text = report(d, bad)
    (out / "breakout-report.md").write_text(text)
    print(text)
    print(f"wrote {out/'x68000-breakout.net'}, {out/'breakout-report.md'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
