"""The logic board: FPGA, memory and MIDI, behind the breakout.

    python3 hw/logic.py          # -> build/x68000-logic.net, .cst, report

What this is, precisely: **the other half of the single-card design**. The full
X68000 MIDI card in `netlist.py` carries the edge fingers, six level
translators, the FPGA, its in-package 8 MB PSRAM and the MIDI drivers all on one
150 mm board. Split it along the translators and you get the breakout (fingers,
translators, plane split) and this (everything after them). The two boards sit
nose to tail in one slot, joined by the two right-angle ribbons.

Why bother splitting a card whose netlist is already finished: the bus half is
the part that is hardest to get right and cheapest to freeze, and the logic half
is the part that will be revised. Respinning a 69 x 150 mm board beats
respinning a 150 x 150 mm one, and the breakout is the same PCB whether the
other end is a Tang Console on the bench or this board in the machine.

The pin budget is what makes the arrangement work at all, and it is exact:

* **45 of 47** usable 3.3 V pins (banks 1 and 2) — 16 data, A16-A23, eight
  control inputs, three open-drain outputs, the bus direction, three MIDI lines,
  four DIP switches, the oscillator and a status LED.
* **15 of 15** usable 1.8 V pins (bank 3) — A1-A15, and nothing else, because
  bank 3 carries the PSRAM and has JTAG, RECONFIG_N and the MODE straps in it.

That is also why the breakout needs two reference voltages: those fifteen
address lines have to arrive at 1.8 V and everything else at 3.3 V.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from breakout import J2_SIGNALS, J3_SIGNALS  # noqa: E402
from netlist import (FpgaPins, Design, check, emit_cst,  # noqa: E402
                     emit_netlist, load_fpga_pins, renumber)

ROOT = Path(__file__).resolve().parent.parent

#: Bus signals as they arrive on the ribbons, in the breakout's own naming.
HOST = {n: f"H{n}" for n in J2_SIGNALS + J3_SIGNALS}
HOST.update({f"SD{i}": f"HD{i}" for i in range(16)})
HOST.update({f"SA{i}": f"HA{i}" for i in range(1, 24)})
HOST["BUS_DIR"] = "BUS_DIR"
for passthrough in ("EXACK_N", "BR_N", "BG_N"):
    HOST[passthrough] = passthrough

#: A1-A15 are the 1.8 V group; see the module docstring.
LOW_ADDRESS = [f"HA{i}" for i in range(1, 16)]


def header(d: Design, ref: str, signals: list[str], note: str) -> None:
    """Mirror of the breakout's header: same pins, same order, same names."""
    d.add(ref, "IDC40", f"2x20 IDC right-angle <- {note}")
    d.wire("+3V3", ref, "p1")          # VREF: we are the host now
    d.wire("+5V_SLOT", ref, "p2")      # the slot's 5 V — needs JP3 on the breakout
    d.wire("+1V8", ref, "p3")          # VREF_LOW
    pins = list(range(4, 39))
    grounds = [n for n in pins if (n - 4) % 5 == 4]
    slots = [n for n in pins if n not in grounds]
    for n in grounds + [39, 40]:
        d.wire("GND", ref, f"p{n}")
    for n, net in zip(slots, signals):
        d.wire(net, ref, f"p{n}")
    for n in slots[len(signals):]:
        d.wire("GND", ref, f"p{n}")


def build() -> tuple[Design, FpgaPins]:
    d = Design()
    fp = FpgaPins()

    # --- FPGA ------------------------------------------------------------
    fpga_pins = {r["pin"]: [r["pin"]] for r in load_fpga_pins()}
    fpga_pins["EP"] = ["89"]
    d.add("U1", "FPGA", "GW1NR-LV9QN88PC6/I5", lcsc="C5799578", pins=fpga_pins)
    for row in load_fpga_pins():
        name, pin = row["name"], row["pin"]
        if name == "VSS":
            d.wire("GND", "U1", pin)
        elif name == "VCC":
            d.wire("+1V2", "U1", pin)
        elif name.startswith("VCCX") or name in ("VCCIO1", "VCCIO2"):
            d.wire("+3V3", "U1", pin)
        elif name == "VCCIO3":
            d.wire("+1V8", "U1", pin)      # the PSRAM's bank
    d.wire("GND", "U1", "EP")

    def fpga(net, voltage="+3V3", banks=("2", "1")):
        d.wire(net, "U1", fp.take(net, voltage, banks))

    # --- the two ribbons -------------------------------------------------
    header(d, "J1", [HOST[n] for n in J2_SIGNALS], "breakout J2: cycle + data")
    header(d, "J2", [HOST[n] for n in J3_SIGNALS], "breakout J3: address")

    for i in range(16):
        fpga(f"HD{i}")
    for net in LOW_ADDRESS:
        fpga(net, "+1V8", ("3",))
    for i in range(16, 24):
        fpga(f"HA{i}")
    for name in ("AS_N", "UDS_N", "LDS_N", "RW", "IACK2_N", "IACK4_N",
                 "EXRESET_N", "IDDIR", "DTACK_N", "IRQ2_N", "IRQ4_N"):
        fpga(f"H{name}")
    fpga("BUS_DIR")

    # Signals the breakout brings over unbuffered at 5 V. They are here for a
    # future bus-master experiment and must not be driven from a 3.3 V pin, so
    # they get test pads and go no further.
    for name in ("HEXREQ_N", "EXACK_N", "BR_N", "BG_N"):
        ref = f"TP{name.strip('H')}"
        d.add(ref, "TP", name)
        d.wire(name, ref, "1")

    # --- MIDI -------------------------------------------------------------
    # Same 5 mA current loop as the single-card design: +5 V through 220 R to
    # the ring, tip through 220 R to an open-drain output.
    d.add("U2", "LVC2G07", "74LVC2G07")
    d.wire("+3V3", "U2", "VCC")
    d.wire("GND", "U2", "GND")
    for n, (jack, r_src, r_snk, drive, sink) in enumerate(
            [("J3", "R1", "R2", "1A", "1Y"), ("J4", "R3", "R4", "2A", "2Y")]):
        d.add(jack, "TRS", f"MIDI OUT{n + 1} TRS-A")
        d.add(r_src, "R", "220")
        d.add(r_snk, "R", "220")
        d.wire(f"F_TX{n + 1}", "U2", drive)
        d.wire(f"TX{n + 1}_SINK", "U2", sink)
        d.wire("+5V_SLOT", r_src, "1")
        d.wire(f"OUT{n + 1}_RING", r_src, "2")
        d.wire(f"OUT{n + 1}_RING", jack, "R")
        d.wire(f"TX{n + 1}_SINK", r_snk, "1")
        d.wire(f"OUT{n + 1}_TIP", r_snk, "2")
        d.wire(f"OUT{n + 1}_TIP", jack, "T")
        d.wire("GND", jack, "S")
        fpga(f"F_TX{n + 1}")

    d.add("J5", "TRS", "MIDI IN TRS-A", dnp=True)
    d.add("U3", "H11L1", "H11L1")
    d.add("R5", "R", "220")
    d.add("R6", "R", "10k")
    d.add("D1", "DIODE", "1N4148")
    d.add("R7", "R", "0R")
    d.wire("IN_RING", "J5", "R")
    d.wire("IN_TIP", "J5", "T")
    d.wire("IN_SLEEVE", "J5", "S")
    d.wire("IN_SLEEVE", "R7", "1")
    d.wire("GND", "R7", "2")
    d.wire("IN_RING", "R5", "1")
    d.wire("IN_LED_A", "R5", "2")
    d.wire("IN_LED_A", "U3", "LED_A")
    d.wire("IN_LED_A", "D1", "K")
    d.wire("IN_TIP", "U3", "LED_K")
    d.wire("IN_TIP", "D1", "A")
    d.wire("+3V3", "U3", "VCC")
    d.wire("GND", "U3", "GND")
    d.wire("F_RX", "U3", "OUT")
    d.wire("+3V3", "R6", "1")
    d.wire("F_RX", "R6", "2")
    fpga("F_RX")

    # --- clock, configuration, switches ----------------------------------
    d.add("X1", "OSC", "27 MHz")
    d.wire("+3V3", "X1", "VCC")
    d.wire("+3V3", "X1", "EN")
    d.wire("GND", "X1", "GND")
    d.wire("CLK27", "X1", "OUT")
    d.wire("CLK27", "U1", "63")             # IOR5A/RPLL_T_in, fixed
    fp.assigned["CLK27"] = ("63", "1")

    d.add("J6", "JTAG", "JTAG 2x5 1.27mm")
    d.wire("+1V8", "J6", "VREF")
    # Pin 7 is the Cortex header's KEY position, usually removed and otherwise
    # left floating. Tying it to ground is the convention and it stops a GND
    # track routed past it from being a clearance error against a netless pad.
    for g in ("GND1", "GND2", "GND3", "KEY"):
        d.wire("GND", "J6", g)
    for sig, pin in (("TMS", "5"), ("TCK", "6"), ("TDI", "7"), ("TDO", "8")):
        d.wire(f"JTAG_{sig}", "J6", sig)
        d.wire(f"JTAG_{sig}", "U1", pin)
    d.wire("RECONFIG_N", "J6", "RESET")
    d.wire("RECONFIG_N", "U1", "9")
    d.add("R8", "R", "10k")
    d.wire("+1V8", "R8", "1")
    d.wire("RECONFIG_N", "R8", "2")
    for ref, pin, name in (("R9", "88", "MODE0"), ("R10", "87", "MODE1")):
        d.add(ref, "R", "10k")
        d.wire("+1V8", ref, "1")
        d.wire(name, ref, "2")
        d.wire(name, "U1", pin)

    d.add("SW1", "DIP4", "RAM bank enable")
    for i in range(1, 5):
        d.wire(f"F_DIP{i}", "SW1", f"a{i}")
        d.wire("GND", "SW1", f"b{i}")
        fpga(f"F_DIP{i}")

    d.add("D2", "LED", "status")
    d.add("R11", "R", "1k")
    d.wire("+3V3", "D2", "A")
    d.wire("LED_K", "D2", "K")
    d.wire("LED_K", "R11", "1")
    d.wire("F_LED", "R11", "2")
    fpga("F_LED")

    # --- rails -------------------------------------------------------------
    # The slot's 5 V arrives over the ribbon and is the only input.
    # The AP2127K-1.8 and -1.2 this started with are not buyable: neither is
    # on LCSC, Future shows zero global stock on the 1.8 V part with a 16-week
    # lead, and RS has discontinued it. The ME6211 family replaces both -- same
    # SOT-23-5, same pinout (VIN/GND/CE/NC/VOUT), and it is what Sipeed puts on
    # the Tang Nano 9K's 1.2 V core, which is this same GW1NR-9.
    #
    # The 3.3 V rail is a different package on purpose. It feeds the other two,
    # so it carries ~255 mA at a 1.7 V drop from the slot's 5 V: 0.43 W, which
    # a SOT-23-5 cannot shed (65-110 K of rise, inside a closed case). The
    # AMS1117 in SOT-223 has a 2.0 x 3.8 mm tab and is a JLCPCB Basic part.
    # It has no enable pin, hence the LDO3 kind.
    for ref, kind, part, lcsc, vin, vout in (
            ("U4", "LDO3", "AMS1117-3.3", "C6186", "+5V_SLOT", "+3V3"),
            ("U5", "LDO", "ME6211C18M5G-N", "C236671", "+3V3", "+1V8"),
            ("U6", "LDO", "ME6211C12M5G-N", "C236672", "+3V3", "+1V2")):
        d.add(ref, kind, part, lcsc=lcsc)
        d.wire(vin, ref, "VIN")
        if "EN" in d.comps[ref]["pins"]:
            d.wire(vin, ref, "EN")      # tie enable high; AMS1117 has none
        d.wire(vout, ref, "VOUT")
        d.wire("GND", ref, "GND")

    # Bulk capacitance belongs at its regulator, not wherever a shared rail
    # happens to lead: the first version of the placer put all three
    # regulators' bulk caps 75 mm away from them, next to the FPGA.
    for i, (net, home) in enumerate((("+5V_SLOT", "U4"), ("+3V3", "U4"),
                                     ("+1V8", "U5"), ("+1V2", "U6"))):
        d.add(f"CB{i}", "C", "10uF")
        d.wire(net, f"CB{i}", "1")
        d.wire("GND", f"CB{i}", "2")
        d.near[f"CB{i}"] = home
    d.decouple("CA", 4, "+3V3", near="U1")   # FPGA banks 1/2 and VCCX
    d.decouple("CL", 1, "+1V8", near="U1")   # bank 3 and the PSRAM
    d.decouple("CC", 4, "+1V2", near="U1")   # core
    # One each for the '07, the opto and the oscillator -- three different
    # devices on the same rail, which is exactly the case a rail-following
    # placer gets wrong.
    d.decouple("CM1_", 1, "+3V3", near="U2")
    d.decouple("CM2_", 1, "+3V3", near="U3")
    d.decouple("CM3_", 1, "+3V3", near="X1")

    # --- what to actually order -------------------------------------------
    #
    # (kind, value) -> LCSC number. Every one of these was read off the
    # JLCPCB/LCSC part page, not recalled: the first attempt at this BOM
    # specified two regulators that are not manufactured in any quantity any
    # more, which is the failure mode worth guarding against.
    #
    # The rule for what goes to the assembler: a part whose *footprint on this
    # board* has been checked against the part you can actually buy. Everything
    # through-hole, and the one SMD part whose land pattern has not been
    # checked, is marked `hand` -- it stays on the board and in the netlist and
    # comes off the CPL, to be soldered here. For five boards that is minutes
    # of work and it removes the whole class of "the footprint did not match".
    LCSC = {
        ("C", "100nF"): "C14663",        # CC0603KRX7R9BB104, 50 V X7R, Basic
        ("C", "10uF"): "C19702",         # CL10A106KP8NNNC, 10 V X5R, Basic
        ("R", "10k"): "C25804",          # 0603WAF1002T5E, 1%, Basic
        ("R", "1k"): "C21190",           # 0603WAF1001T5E, 1%, Basic
        ("R", "220"): "C22962",          # 0603WAF2200T5E, 1%, Basic
        ("R", "0R"): "C21189",           # 0603WAF0000T5E jumper, Basic
        ("DIODE", "1N4148"): "C81598",   # 1N4148W, SOD-123
        ("LED", "status"): "C2286",      # KT-0603R, red
        ("LVC2G07", "74LVC2G07"): "C37708",   # SN74LVC2G07DBVR, TI, SOT-23-6
        ("OSC", "27 MHz"): "C507381",    # TXC 7X27000043, SMD3225-4P, +-50 ppm
    }

    #: Fitted, but soldered here rather than by the assembler. The reason is
    #: recorded because "why is this not on the CPL" is the first question
    #: anyone will ask.
    HAND = {
        "J1": "through-hole 2x20 IDC",
        "J2": "through-hole 2x20 IDC",
        "J3": "through-hole 3.5 mm jack",
        "J4": "through-hole 3.5 mm jack",
        "J5": "through-hole 3.5 mm jack",
        "U3": "DIP-6 opto; through-hole",
        "SW1": "through-hole DIP switch",
        # 1.27 mm SMD, and the land pattern here has *not* been checked against
        # a buyable part. C5160761 (DEALON DZ127S-22-10-55, 2x5P 1.27 mm SMD)
        # is the candidate; verify its drawing against
        # PinHeader_2x05_P1.27mm_Vertical_SMD before putting it on the CPL.
        "J6": "fine-pitch header, footprint unverified",
    }

    for ref, comp in d.comps.items():
        key = (comp["kind"], comp["value"])
        if not comp["lcsc"] and key in LCSC:
            comp["lcsc"] = LCSC[key]
        if ref in HAND:
            comp["hand"] = True
            comp["hand_reason"] = HAND[ref]

    d.notes += [
        "The breakout supplies VREF (3.3 V) on header pin 1 and VREF_LOW "
        "(1.8 V) on pin 3; this board sources both, because it is the host.",
        "~EXACK, ~BR and ~BG arrive unbuffered at 5 V and stop at test pads. "
        "Driving them from a 3.3 V pin would need its own translator.",
        "DIP switches use the FPGA's internal pull-ups (PULL_MODE=UP).",
        "JTAG is in bank 3, so the programmer must drive 1.8 V; J6 pin 1 "
        "carries 1.8 V as VREF for adapters that follow it.",
    ]
    return d, fp


def report(d: Design, fp: FpgaPins, bad: list[str]) -> str:
    used: dict[str, int] = {}
    for _, (_, bank) in fp.assigned.items():
        used[bank] = used.get(bank, 0) + 1
    free = fp.remaining()
    lines = ["# Logic board review", "",
             f"{len(d.comps)} components, {len(d.nets)} nets.", "",
             "## FPGA pins"]
    for bank in sorted(set(list(used) + list(free))):
        volt = "1.8 V" if bank == "3" else "3.3 V"
        lines.append(f"* bank {bank} ({volt}): {used.get(bank, 0)} used, "
                     f"{free.get(bank, 0)} free")
    lines += ["", "## Notes"] + [f"* {n}" for n in d.notes] + ["", "## Checks"]
    if bad:
        lines.append(f"**{len(bad)} problem(s):**")
        lines += [f"* {b}" for b in bad]
    else:
        lines.append("All checks pass.")
    return "\n".join(lines) + "\n"


def main() -> int:
    d, fp = build()
    renumber(d)
    bad = check(d, fp)
    out = ROOT / "build"
    out.mkdir(exist_ok=True)
    (out / "x68000-logic.net").write_text(emit_netlist(d))
    (out / "x68000-logic.cst").write_text(emit_cst(fp))
    text = report(d, fp, bad)
    (out / "logic-report.md").write_text(text)
    print(text)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
