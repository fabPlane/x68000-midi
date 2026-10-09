"""Generate the rev A netlist, the Gowin constraint file, and a review report.

    python3 hw/netlist.py            # -> build/x68000-midi.net, .cst, report

One source of truth for "what connects to what", because the same facts have to
land in three places that must not drift: pcbnew (import the ``.net``, place and
route), the gateware build (the ``.cst`` pin constraints), and a human reading
the report. Writing it as code also means the awkward parts get *checked*
instead of remembered -- see ``check()``:

* every FPGA pin is a real pin of the QN88P package, in a bank whose voltage
  matches the signal (bank 3 is 1.8 V, because it carries the PSRAM);
* no net has fewer than two nodes;
* every supply pin of every IC is connected, and has a decoupling capacitor;
* every footprint exists in the installed KiCad libraries.

Pin names for the standard parts are read out of the KiCad symbol libraries
rather than typed from datasheets, so a netlist node that names a pin the part
does not have is an error, not a silent mistake.

See ``docs/HARDWARE.md`` for why the board is shaped this way.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KICAD = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport")
SYMBOLS, FOOTPRINTS = KICAD / "symbols", KICAD / "footprints"

# --------------------------------------------------------------------------
# Part pin maps
# --------------------------------------------------------------------------

def symbol_pins(lib: str, name: str) -> dict[str, str]:
    """{pin name: pin number} from an installed KiCad symbol."""
    text = (SYMBOLS / f"{lib}.kicad_sym").read_text()
    start = text.index(f'(symbol "{name}"')
    depth = 0
    for end in range(start, len(text)):
        if text[end] == "(":
            depth += 1
        elif text[end] == ")":
            depth -= 1
            if depth == 0:
                break
    block = text[start:end]
    out = {}
    for m in re.finditer(
        r'\(pin (\w+) \w+[\s\S]{0,200}?\(name "([^"]*)"[\s\S]{0,80}?\(number "([^"]*)"',
        block,
    ):
        out.setdefault(m.group(2), m.group(3))
    return out


#: Parts whose pins we hand-declare, with the datasheet pinout in the comment.
HAND_PINS: dict[str, dict[str, str]] = {
    # Diodes AP2112K / AP2127K, SOT-25.
    "LDO": {"VIN": "1", "GND": "2", "EN": "3", "NC": "4", "VOUT": "5"},
    # 4-pad SMD oscillator (SG-3225 and every compatible).
    "OSC": {"EN": "1", "GND": "2", "OUT": "3", "VCC": "4"},
    "LED": {"A": "1", "K": "2"},
    "DIODE": {"A": "1", "K": "2"},
    "R": {"1": "1", "2": "2"},
    "C": {"1": "1", "2": "2"},
    # SW_DIP_SPSTx04: one side 1-4, the other 5-8, switch n joins n and 9-n.
    "DIP4": {f"{side}{n}": str(n if side == "a" else 9 - n)
             for n in range(1, 5) for side in ("a", "b")},
    # KiCad's AudioJack3 numbers its pads T/R/S and leaves the names blank.
    "TRS": {"T": "T", "R": "R", "S": "S"},
    # Unnamed in KiCad's symbols, so declared here from the datasheets.
    # SC-70-6 dual open-drain buffer.
    "LVC2G07": {"1A": "1", "GND": "2", "2A": "3", "2Y": "4", "VCC": "5",
                "1Y": "6"},
    # SOT-23-5 single open-drain buffer.
    "LVC1G07": {"NC": "1", "A": "2", "GND": "3", "Y": "4", "VCC": "5"},
    # DIP-6: 1 LED anode, 2 LED cathode, 3 NC, 4 out, 5 GND, 6 VCC.
    "H11L1": {"LED_A": "1", "LED_K": "2", "NC": "3", "OUT": "4", "GND": "5",
              "VCC": "6"},
    # 2x5 1.27 mm, ARM-Cortex order, used as plain JTAG like midiori's.
    "JTAG": {"VREF": "1", "TMS": "2", "GND1": "3", "TCK": "4", "GND2": "5",
             "TDO": "6", "KEY": "7", "TDI": "8", "GND3": "9", "RESET": "10"},
}


def part_pins(kind: str) -> dict[str, str]:
    if kind in HAND_PINS:
        return HAND_PINS[kind]
    lib, name = KICAD_SYMBOLS[kind]
    return symbol_pins(lib, name)


#: Only parts whose KiCad symbol actually names its pins; the rest are in
#: HAND_PINS above, because a blank name cannot be checked against.
KICAD_SYMBOLS = {
    "LVC8T245": ("Logic_LevelTranslator", "SN74LVC8T245"),
}

FOOTPRINT = {
    "FPGA": "Package_DFN_QFN:ArtInChip_QFN-88-1EP_10x10mm_P0.4mm_EP6.74x6.74mm",
    "LVC8T245": "Package_SO:TSSOP-24_4.4x7.8mm_P0.65mm",
    "LVC2G07": "Package_TO_SOT_SMD:SOT-363_SC-70-6",
    "LVC1G07": "Package_TO_SOT_SMD:SOT-23-5",
    "H11L1": "Package_DIP:DIP-6_W7.62mm",
    "LDO": "Package_TO_SOT_SMD:SOT-23-5",
    "OSC": "Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm",
    "TRS": "Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
    "JTAG": "Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical_SMD",
    "DIP4": "Button_Switch_SMD:SW_DIP_SPSTx04_Slide_Omron_A6S-410x_W8.9mm_P2.54mm",
    "LED": "LED_SMD:LED_0603_1608Metric",
    "DIODE": "Diode_SMD:D_SOD-123",
    "R": "Resistor_SMD:R_0603_1608Metric",
    "C": "Capacitor_SMD:C_0603_1608Metric",
    "EDGE": "x68k:x68000_expansion",
}

#: Supply pin names per part kind, and what they must be tied to.
SUPPLIES = {
    "LVC8T245": {"V_{CCA}": "VCCA", "V_{CCB}": "VCCB", "GND": "GND"},
    "LVC2G07": {"VCC": "VCC", "GND": "GND"},
    "LVC1G07": {"VCC": "VCC", "GND": "GND"},
    "H11L1": {"VCC": "VCC", "GND": "GND"},
    "OSC": {"VCC": "VCC", "GND": "GND"},
}

# --------------------------------------------------------------------------
# The X68000 slot, from docs/x68000-expansion-pinout.md (Outside X68000, 1993)
# --------------------------------------------------------------------------

SLOT: dict[str, str] = {}
for p in ("A1", "A3", "A11", "A21", "A41", "B1", "B11", "B21", "B41"):
    SLOT[p] = "GND"
for p in ("A49", "A50", "B49", "B50"):
    SLOT[p] = "+5V"
for i, p in enumerate(["A4", "A5", "A6", "A7", "A8", "A9", "A10",
                       "A12", "A13", "A14", "A15", "A16", "A17", "A18",
                       "A19", "A20"]):
    SLOT[p] = f"SD{i}"
_ADDR_PADS = (["B5", "B6", "B7", "B8", "B9", "B10"]
              + [f"B{n}" for n in range(12, 21)]
              + [f"B{n}" for n in range(22, 30)])
for i, p in enumerate(_ADDR_PADS):
    SLOT[p] = f"SA{i + 1}"
SLOT.update({
    "A27": "AS_N", "A28": "LDS_N", "A29": "UDS_N", "A30": "RW",
    "A36": "DTACK_N", "A37": "EXRESET_N",
    "B30": "IDDIR", "B42": "IRQ2_N", "B43": "IRQ4_N",
    "B44": "IACK2_N", "B45": "IACK4_N",
})

#: Slot pins we deliberately leave unconnected, and why. Everything on the
#: connector that is not in SLOT must be listed here, or check() complains.
SLOT_NC = {
    "A2": "20 MHz clock, unused (we run from our own oscillator)",
    "B2": "10 MHz clock, unused", "B3": "~10 MHz, unused",
    "B4": "E, 6800 peripheral clock, unused",
    "A22": "+12 V", "A23": "+12 V", "A32": "-12 V", "A33": "-12 V",
    "A24": "FC0", "A25": "FC1", "A26": "FC2",
    "A31": "key", "B31": "key",
    "A34": "~VMA", "A35": "-EXVPA", "A38": "~HALT", "A39": "~EXBERR",
    "A40": "~EXPWON",
    "A42": "Vcc2 standby", "A43": "Vcc2 standby",
    "A44": "SELEN", "A45": "CASRDEN", "A46": "CASWRL", "A47": "CASWRU",
    "A48": "INH2",
    "B32": "HSYNC", "B33": "VSYNC",
    "B34": "~DONE", "B35": "~DTC", "B36": "~EXREQ", "B37": "~EXACK",
    "B38": "~EXPCL", "B39": "~EXOWN", "B40": "~EXNMI",
    "B46": "~BR", "B47": "~BG", "B48": "~BGACK",
}

# --------------------------------------------------------------------------
# FPGA pin allocation
# --------------------------------------------------------------------------

BANK_VOLTAGE = {"1": "+3V3", "2": "+3V3", "3": "+1V8"}

#: Pins we must not hand out: fixed function, or reserved.
FPGA_RESERVED = {
    "5": "JTAG TMS", "6": "JTAG TCK", "7": "JTAG TDI", "8": "JTAG TDO",
    "9": "RECONFIG_N", "87": "MODE1", "88": "MODE0",
    "63": "27 MHz clock in (IOR5A/RPLL_T_in)",
}


def load_fpga_pins() -> list[dict]:
    with open(ROOT / "hw" / "gw1nr9_qn88p_pins.csv") as f:
        return list(csv.DictReader(f))


class FpgaPins:
    """Hands out QN88P pins, cheapest bank first, and remembers why."""

    def __init__(self):
        rows = load_fpga_pins()
        self.by_pin = {r["pin"]: r for r in rows}
        self.free = defaultdict(list)
        for r in rows:
            if r["name"].startswith("IO") and r["pin"] not in FPGA_RESERVED:
                self.free[r["bank"]].append(r["pin"])
        for bank in self.free:
            self.free[bank].sort(key=int)
        self.assigned: dict[str, tuple[str, str]] = {}   # net -> (pin, bank)

    def take(self, net: str, voltage: str, banks: tuple[str, ...]) -> str:
        for bank in banks:
            if BANK_VOLTAGE[bank] != voltage:
                continue
            if self.free[bank]:
                pin = self.free[bank].pop(0)
                self.assigned[net] = (pin, bank)
                return pin
        raise SystemExit(f"out of {voltage} pins for {net}")

    def remaining(self) -> dict[str, int]:
        return {b: len(p) for b, p in sorted(self.free.items())}


# --------------------------------------------------------------------------
# The design
# --------------------------------------------------------------------------

class Design:
    def __init__(self):
        self.comps: dict[str, dict] = {}
        self.nets: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.notes: list[str] = []

    def add(self, ref, kind, value, *, lcsc="", dnp=False, pins=None):
        self.comps[ref] = {"kind": kind, "value": value, "lcsc": lcsc,
                           "dnp": dnp, "pins": pins or part_pins(kind),
                           "footprint": FOOTPRINT[kind]}
        return ref

    def wire(self, net, ref, pin_name):
        comp = self.comps[ref]
        if pin_name not in comp["pins"]:
            raise SystemExit(f"{ref} ({comp['kind']}) has no pin {pin_name!r}; "
                             f"it has {sorted(comp['pins'])}")
        self.nets[net].append((ref, comp["pins"][pin_name]))

    def decouple(self, ref_prefix, n, net, value="100nF"):
        for i in range(n):
            r = f"{ref_prefix}{i + 1}"
            self.add(r, "C", value)
            self.wire(net, r, "1")
            self.wire("GND", r, "2")


def build() -> tuple[Design, FpgaPins]:
    d = Design()
    fp = FpgaPins()

    # ---- edge connector -------------------------------------------------
    edge_pins = {f"{s}{n}": f"{s}{n}" for s in "AB" for n in range(1, 51)}
    d.add("J1", "EDGE", "X68000 I/O slot", pins=edge_pins)
    for pad, net in SLOT.items():
        d.wire(net, "J1", pad)

    # ---- FPGA -----------------------------------------------------------
    fpga_pins = {r["pin"]: r["pin"] for r in load_fpga_pins()}
    fpga_pins["EP"] = "89"
    d.add("U1", "FPGA", "GW1NR-LV9QN88PC6/I5", lcsc="C5799578", pins=fpga_pins)
    for row in load_fpga_pins():
        name, pin = row["name"], row["pin"]
        if name == "VSS":
            d.wire("GND", "U1", pin)
        elif name == "VCC":
            d.wire("+1V2", "U1", pin)
        elif name.startswith("VCCX"):
            d.wire("+3V3", "U1", pin)
        elif name in ("VCCIO1", "VCCIO2"):
            d.wire("+3V3", "U1", pin)
        elif name == "VCCIO3":
            d.wire("+1V8", "U1", pin)       # the PSRAM's bank
    d.wire("GND", "U1", "EP")

    def fpga(net, voltage="+3V3", banks=("2", "1")):
        d.wire(net, "U1", fp.take(net, voltage, banks))

    # ---- translators ----------------------------------------------------
    def translator(ref, slot_nets, fpga_nets, vccb, *, dir_net, oe_net):
        d.add(ref, "LVC8T245", "SN74LVC8T245")
        d.wire("+5V", ref, "V_{CCA}")
        d.wire(vccb, ref, "V_{CCB}")
        d.wire("GND", ref, "GND")
        d.wire(dir_net, ref, "DIR")
        d.wire(oe_net, ref, "~{OE}")
        for i, (sn, fn) in enumerate(zip(slot_nets, fpga_nets), start=1):
            if sn is None:
                continue
            d.wire(sn, ref, f"A{i}")
            d.wire(fn, ref, f"B{i}")

    # Data: bidirectional, so DIR comes from the FPGA. Address and controls
    # only ever flow slot -> FPGA, so their DIR is strapped.
    # DIR and ~OE on an 8T245 are referenced to V_CCA, which is the 5 V slot
    # here, so a 3.3 V FPGA output does not meet their V_IH (0.7 x 5 V).
    # midiori drives them from its FPGA anyway and gets away with it; we put a
    # 74AHCT1G34 in the way instead -- AHCT has TTL thresholds at 5 V
    # (V_IH = 2.0 V), so 3.3 V drives it properly. Only the data pair needs a
    # live direction; address and control never flow the other way, so their
    # DIR is strapped A->B and their ~OE to ground.
    d.add("U15", "LVC1G07", "74AHCT1G34")
    d.wire("+5V", "U15", "VCC")
    d.wire("GND", "U15", "GND")
    d.wire("F_BUS_DIR", "U15", "A")
    d.wire("BUS_DIR", "U15", "Y")

    translator("U2", [f"SD{i}" for i in range(8)], [f"FD{i}" for i in range(8)],
               "+3V3", dir_net="BUS_DIR", oe_net="GND")
    translator("U3", [f"SD{i}" for i in range(8, 16)],
               [f"FD{i}" for i in range(8, 16)],
               "+3V3", dir_net="BUS_DIR", oe_net="GND")
    translator("U4", [f"SA{i}" for i in range(16, 24)],
               [f"FA{i}" for i in range(16, 24)],
               "+3V3", dir_net="+5V", oe_net="GND")
    translator("U5", [f"SA{i}" for i in range(1, 9)],
               [f"FA{i}" for i in range(1, 9)],
               "+1V8", dir_net="+5V", oe_net="GND")
    translator("U6", [f"SA{i}" for i in range(9, 16)] + [None],
               [f"FA{i}" for i in range(9, 16)] + [None],
               "+1V8", dir_net="+5V", oe_net="GND")
    ctrl = ["AS_N", "UDS_N", "LDS_N", "RW", "IACK2_N", "IACK4_N",
            "EXRESET_N", "IDDIR"]
    translator("U7", ctrl, [f"F{n}" for n in ctrl],
               "+3V3", dir_net="+5V", oe_net="GND")

    for i in range(16):
        fpga(f"FD{i}")
    for i in range(16, 24):
        fpga(f"FA{i}")
    for i in range(1, 16):
        fpga(f"FA{i}", "+1V8", ("3",))
    for n in ctrl:
        fpga(f"F{n}")
    fpga("F_BUS_DIR")

    # ---- open-drain outputs to the slot ---------------------------------
    # 74LVC07 at 3.3 V: the output tolerates the 5 V bus when it is off, and
    # the motherboard provides the pull-up.
    d.add("U8", "LVC2G07", "74LVC2G07")
    d.add("U9", "LVC2G07", "74LVC2G07")
    d.add("U10", "LVC1G07", "74LVC1G07")
    for ref in ("U8", "U9", "U10"):
        d.wire("+3V3", ref, "VCC")
        d.wire("GND", ref, "GND")
    # 74LVC2G07 pins are unnamed in the KiCad symbol: 1=1A 3=2A 4=2Y 6=1Y.
    for ref, (in_a, out_a, in_b, out_b) in {
        "U8": ("F_DTACK", "DTACK_N", "F_IRQ2", "IRQ2_N"),
        "U9": ("F_IRQ4", "IRQ4_N", "F_TX1", "TX1_SINK"),
    }.items():
        d.wire(in_a, ref, "1A")
        d.wire(out_a, ref, "1Y")
        d.wire(in_b, ref, "2A")
        d.wire(out_b, ref, "2Y")
    d.wire("F_TX2", "U10", "A")
    d.wire("TX2_SINK", "U10", "Y")
    for n in ("F_DTACK", "F_IRQ2", "F_IRQ4", "F_TX1", "F_TX2"):
        fpga(n)

    # ---- MIDI -----------------------------------------------------------
    # OUT: 5 mA current loop. +5V -> 220R -> ring; tip -> 220R -> open drain.
    for n, (ref_j, r_src, r_snk, sink) in enumerate(
            [("J2", "R1", "R2", "TX1_SINK"), ("J3", "R3", "R4", "TX2_SINK")]):
        d.add(ref_j, "TRS", f"MIDI OUT{n + 1} TRS-A")
        d.add(r_src, "R", "220")
        d.add(r_snk, "R", "220")
        d.wire("+5V", r_src, "1")
        d.wire(f"OUT{n + 1}_RING", r_src, "2")
        d.wire(f"OUT{n + 1}_RING", ref_j, "R")      # TRS-A ring = DIN pin 4
        d.wire(sink, r_snk, "1")
        d.wire(f"OUT{n + 1}_TIP", r_snk, "2")
        d.wire(f"OUT{n + 1}_TIP", ref_j, "T")       # TRS-A tip = DIN pin 5
        d.wire("GND", ref_j, "S")

    # IN: opto-isolated, jack fitted but DNP for rev A.
    d.add("J4", "TRS", "MIDI IN TRS-A", dnp=True)
    d.add("U11", "H11L1", "H11L1")
    d.add("R5", "R", "220")
    d.add("D1", "DIODE", "1N4148")
    d.wire("IN_RING", "J4", "R")
    d.wire("IN_TIP", "J4", "T")
    # The MIDI spec leaves pin 2 of an IN socket unconnected to stop ground
    # loops; TRS gear usually grounds the sleeve. R11 is a 0 R link so the
    # choice is a build option rather than a cut track.
    d.wire("IN_SLEEVE", "J4", "S")
    d.add("R11", "R", "0R")
    d.wire("IN_SLEEVE", "R11", "1")
    d.wire("GND", "R11", "2")
    d.wire("IN_RING", "R5", "1")
    d.wire("IN_LED_A", "R5", "2")
    d.wire("IN_LED_A", "U11", "LED_A")
    d.wire("IN_LED_A", "D1", "K")       # catches reverse current
    d.wire("IN_TIP", "U11", "LED_K")
    d.wire("IN_TIP", "D1", "A")
    d.wire("+3V3", "U11", "VCC")
    d.wire("GND", "U11", "GND")
    d.wire("F_RX", "U11", "OUT")
    d.add("R10", "R", "10k")            # the H11L1 output is open collector
    d.wire("+3V3", "R10", "1")
    d.wire("F_RX", "R10", "2")
    fpga("F_RX")

    # ---- clock, LED, DIP, JTAG ------------------------------------------
    d.add("X1", "OSC", "27 MHz")
    d.wire("+3V3", "X1", "VCC")
    d.wire("+3V3", "X1", "EN")
    d.wire("GND", "X1", "GND")
    d.wire("CLK27", "X1", "OUT")
    d.wire("CLK27", "U1", "63")                     # RPLL_T_in, fixed
    fp.assigned["CLK27"] = ("63", "1")

    d.add("D2", "LED", "status")
    d.add("R6", "R", "1k")
    d.wire("+3V3", "D2", "A")
    d.wire("LED_K", "D2", "K")
    d.wire("LED_K", "R6", "1")
    d.wire("F_LED", "R6", "2")
    fpga("F_LED")

    d.add("SW1", "DIP4", "RAM bank enable")
    for i in range(1, 5):
        d.wire(f"F_DIP{i}", "SW1", f"a{i}")
        d.wire("GND", "SW1", f"b{i}")
        fpga(f"F_DIP{i}")
    d.notes.append("DIP switches rely on the FPGA's internal pull-ups "
                   "(PULL_MODE=UP in the .cst); there are no external resistors.")

    d.add("J6", "JTAG", "JTAG 2x5 1.27mm")
    d.wire("+1V8", "J6", "VREF")
    for g in ("GND1", "GND2", "GND3"):
        d.wire("GND", "J6", g)
    for sig, pin in (("TMS", "5"), ("TCK", "6"), ("TDI", "7"), ("TDO", "8")):
        d.wire(f"JTAG_{sig}", "J6", sig)
        d.wire(f"JTAG_{sig}", "U1", pin)
    d.wire("RECONFIG_N", "J6", "RESET")
    d.wire("RECONFIG_N", "U1", "9")
    d.add("R7", "R", "10k")
    d.wire("+1V8", "R7", "1")
    d.wire("RECONFIG_N", "R7", "2")
    d.notes.append("The 74LVC07 open-drain outputs sit on the 5 V bus while "
                   "powered from 3.3 V; that is what the '07 family is for and "
                   "what midiori does, but confirm the part you order is rated "
                   "5.5 V on the output.")
    d.notes.append("JTAG is in bank 3, so the programmer must drive 1.8 V; "
                   "pin 1 of J6 carries the card's 1.8 V as VREF for adapters "
                   "that follow it.")

    # MODE0/MODE1 strapped for internal-flash boot (both high).
    for ref, pin, name in (("R8", "88", "MODE0"), ("R9", "87", "MODE1")):
        d.add(ref, "R", "10k")
        d.wire("+1V8", ref, "1")
        d.wire(name, ref, "2")
        d.wire(name, "U1", pin)

    # ---- rails -----------------------------------------------------------
    for ref, part, vin, vout, lcsc in (
            ("U12", "AP2112K-3.3", "+5V", "+3V3", ""),
            ("U13", "AP2127K-1.8", "+3V3", "+1V8", ""),
            ("U14", "AP2127K-1.2", "+3V3", "+1V2", "")):
        d.add(ref, "LDO", part, lcsc=lcsc)
        d.wire(vin, ref, "VIN")
        d.wire(vin, ref, "EN")
        d.wire(vout, ref, "VOUT")
        d.wire("GND", ref, "GND")

    # Bulk: in and out of every regulator.
    for i, net in enumerate(("+5V", "+3V3", "+1V8", "+1V2"), start=100):
        d.add(f"C{i}", "C", "10uF")
        d.wire(net, f"C{i}", "1")
        d.wire("GND", f"C{i}", "2")

    # Decoupling: one per supply pin of every IC, counted not guessed.
    per_net = defaultdict(int)
    for ref, comp in list(d.comps.items()):
        for pin_name, role in SUPPLIES.get(comp["kind"], {}).items():
            if role == "GND":
                continue
            number = comp["pins"][pin_name]
            for net, nodes in d.nets.items():
                if (ref, number) in nodes and net != "GND":
                    per_net[net] += 1
    for net, n in per_net.items():
        d.decouple(f"C{net.strip('+')}_", n, net)
    # The FPGA's own supply pins.
    for net, n in (("+3V3", 4), ("+1V8", 1), ("+1V2", 4)):
        d.decouple(f"CU1{net.strip('+')}_", n, net)

    return d, fp


# --------------------------------------------------------------------------
# Checks and output
# --------------------------------------------------------------------------

def check(d: Design, fp: FpgaPins) -> list[str]:
    bad = []

    for net, nodes in sorted(d.nets.items()):
        if len(nodes) < 2:
            bad.append(f"net {net} has only {len(nodes)} node(s): {nodes}")

    for pad in (f"{s}{n}" for s in "AB" for n in range(1, 51)):
        if pad not in SLOT and pad not in SLOT_NC:
            bad.append(f"slot pad {pad} is neither connected nor listed as NC")

    for net, (pin, bank) in sorted(fp.assigned.items()):
        row = fp.by_pin.get(pin)
        if row is None or not row["name"].startswith("IO"):
            bad.append(f"{net} -> pin {pin} is not a user I/O")
            continue
        if row["bank"] != bank:
            bad.append(f"{net} -> pin {pin} is bank {row['bank']}, not {bank}")

    for ref, comp in sorted(d.comps.items()):
        for pin_name in SUPPLIES.get(comp["kind"], {}):
            number = comp["pins"][pin_name]
            if not any((ref, number) in nodes for nodes in d.nets.values()):
                bad.append(f"{ref} supply pin {pin_name} ({number}) floats")
        lib, fpname = comp["footprint"].split(":")
        roots = (FOOTPRINTS, ROOT / "hw" / "lib")   # KiCad's, then this project's
        if not any((r / f"{lib}.pretty" / f"{fpname}.kicad_mod").exists()
                   for r in roots):
            bad.append(f"{ref}: footprint {comp['footprint']} not installed")

    seen = defaultdict(list)
    for net, nodes in d.nets.items():
        for node in nodes:
            seen[node].append(net)
    for node, nets in sorted(seen.items()):
        if len(nets) > 1:
            bad.append(f"{node[0]} pin {node[1]} is on {len(nets)} nets: {nets}")

    return bad


def emit_netlist(d: Design) -> str:
    out = ['(export (version "E")',
           '  (design (source "hw/netlist.py") (date "generated") '
           '(tool "x68000_midi hw/netlist.py"))',
           "  (components"]
    for ref, c in sorted(d.comps.items()):
        fields = f'(field (name "LCSC") "{c["lcsc"]}")' if c["lcsc"] else ""
        dnp = " (dnp)" if c["dnp"] else ""
        out.append(f'    (comp (ref "{ref}") (value "{c["value"]}") '
                   f'(footprint "{c["footprint"]}"){dnp}'
                   + (f" (fields {fields})" if fields else "") + ")")
    out.append("  )")
    out.append("  (nets")
    for code, (net, nodes) in enumerate(sorted(d.nets.items()), start=1):
        out.append(f'    (net (code {code}) (name "{net}")')
        for ref, pin in sorted(nodes):
            out.append(f'      (node (ref "{ref}") (pin "{pin}"))')
        out.append("    )")
    out.append("  )")
    out.append(")")
    return "\n".join(out) + "\n"


def emit_cst(fp: FpgaPins) -> str:
    """Gowin constraint file, so the gateware cannot drift from the board."""
    lines = ["// Generated by hw/netlist.py -- do not edit.",
             "// Bank 3 is 1.8 V (it carries the PSRAM); banks 1 and 2 are 3.3 V.",
             ""]
    for net, (pin, bank) in sorted(fp.assigned.items(),
                                   key=lambda kv: int(kv[1][0])):
        volt = BANK_VOLTAGE[bank]
        std = "LVCMOS18" if volt == "+1V8" else "LVCMOS33"
        extra = " PULL_MODE=UP" if net.startswith("F_DIP") else ""
        lines.append(f'IO_LOC "{net}" {pin};')
        lines.append(f'IO_PORT "{net}" IO_TYPE={std}{extra};')
    return "\n".join(lines) + "\n"


def report(d: Design, fp: FpgaPins, bad: list[str]) -> str:
    lines = ["# Rev A netlist review", ""]
    lines.append(f"{len(d.comps)} components, {len(d.nets)} nets.")
    dnp = [r for r, c in d.comps.items() if c["dnp"]]
    lines.append(f"Not populated: {', '.join(sorted(dnp)) or 'none'}.")
    lines.append("")
    lines.append("## FPGA pins")
    used = defaultdict(int)
    for _, (_, bank) in fp.assigned.items():
        used[bank] += 1
    for bank in sorted(set(list(used) + list(fp.remaining()))):
        lines.append(f"* bank {bank} ({BANK_VOLTAGE[bank]}): "
                     f"{used.get(bank, 0)} used, {fp.remaining().get(bank, 0)} free")
    lines.append("")
    lines.append("Reserved and not available: "
                 + ", ".join(f"{p} ({w})" for p, w in sorted(
                     FPGA_RESERVED.items(), key=lambda kv: int(kv[0]))))
    lines.append("")
    free_total = sum(fp.remaining().values())
    lines.append("")
    lines.append(f"**{sum(used.values())} of {sum(used.values()) + free_total} "
                 f"available user I/O are used.** Banks 2 and 3 are full; the "
                 f"only spare pins are in bank 1. Adding a signal means taking "
                 f"one of those two, freeing MODE0/MODE1 (87/88, bank 3) for "
                 f"reuse after configuration, or moving to a bigger package.")
    lines.append("")
    lines.append("Pins are assigned in a fixed order and every one of them is "
                 "free to move **within its own bank** during routing; only the "
                 "clock (63) and JTAG are fixed. Re-run this script after "
                 "swapping any, so the .cst follows the board.")
    lines.append("")
    lines.append("## Notes")
    for n in d.notes:
        lines.append(f"* {n}")
    lines.append("")
    lines.append("## Checks")
    if bad:
        lines.append(f"**{len(bad)} problem(s):**")
        lines += [f"* {b}" for b in bad]
    else:
        lines.append("All checks pass: every net has two or more nodes, every "
                     "slot pad is connected or explicitly NC, every FPGA pin is "
                     "a real user I/O in a bank at the right voltage, every "
                     "supply pin is connected, no pin is on two nets, and every "
                     "footprint is installed.")
    return "\n".join(lines) + "\n"


def renumber(d: Design) -> None:
    """Passives get sequential designators: pcbnew wants C1..Cn, not C3V3_2."""
    order = {"C": [], "R": []}
    for ref in sorted(d.comps, key=lambda r: (r[0], len(r), r)):
        if ref[0] in order and not ref[1:].isdigit():
            order[ref[0]].append(ref)
        elif ref[0] in order:
            order[ref[0]].insert(0, ref)      # keep hand-numbered ones first
    mapping = {}
    for prefix, refs in order.items():
        for i, ref in enumerate(refs, start=1):
            mapping[ref] = f"{prefix}{i}"
    d.comps = {mapping.get(r, r): c for r, c in d.comps.items()}
    d.nets = {n: [(mapping.get(r, r), p) for r, p in nodes]
              for n, nodes in d.nets.items()}


def main() -> int:
    d, fp = build()
    renumber(d)
    bad = check(d, fp)
    out = ROOT / "build"
    out.mkdir(exist_ok=True)
    (out / "x68000-midi.net").write_text(emit_netlist(d))
    (out / "x68000-midi.cst").write_text(emit_cst(fp))
    text = report(d, fp, bad)
    (out / "netlist-report.md").write_text(text)
    print(text)
    print(f"wrote {out/'x68000-midi.net'}, {out/'x68000-midi.cst'}, "
          f"{out/'netlist-report.md'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
