"""Derive a 2x20 IDC header land with pads small enough to route between.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/make_idc_footprint.py

KiCad's `IDC-Header_2x20_P2.54mm_Horizontal` uses 1.70 mm pads on a 2.54 mm
grid. That leaves **0.84 mm** between neighbours, and two tracks at 0.15 mm
with 0.15 mm clearance need 0.75 mm: nine hundredths of a millimetre of slack,
in the one place on the board where fifty-five signals have to get past a
connector.

Freerouting duly made a mess of it. On the 2026-10-14 run it put HD2 and HD3
through the same gap at (16.9, 120.3) *overlapping* -- a genuine short, not a
clearance nit -- along with a handful of degenerate 0.0001 mm segments, and
left HA18, ~AS, ~EXRESET and ~IRQ2 unrouted. Those four and the short were all
within J1's pad field.

An IDC header post is 0.64 mm square, so a 1.00 mm drill already has room to
spare and the 1.70 mm pad is simply generous. 1.40 mm keeps a 0.20 mm annular
ring -- comfortably over JLCPCB's 0.13 mm minimum for a plated hole -- and
opens the gap to 1.14 mm, which takes three tracks instead of two-and-a-bit.

Only the through-hole pads are touched; the shroud, the silkscreen and the
courtyard are the vendor's.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pcbnew

SRC_LIB = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
               "/Connector_IDC.pretty")
SRC = "IDC-Header_2x20_P2.54mm_Horizontal"
DST_LIB = Path(__file__).resolve().parent / "lib" / "x68k.pretty"
DST = "IDC-Header_2x20_P2.54mm_Horizontal_Narrow"

PAD_D = 1.40     # was 1.70; 2.54 - 1.40 = 1.14 mm between neighbours
DRILL = 1.00     # unchanged: a 0.64 mm square post needs it


def main() -> int:
    fp = pcbnew.FootprintLoad(str(SRC_LIB), SRC)
    if fp is None:
        raise SystemExit(f"{SRC} not found in {SRC_LIB}")
    changed, ring = 0, None
    for pad in fp.Pads():
        drill = pcbnew.ToMM(pad.GetDrillSize().x)
        if drill <= 0:
            continue                      # surface pad, not ours to touch
        pad.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(PAD_D),
                                    pcbnew.FromMM(PAD_D)))
        pad.SetDrillSize(pcbnew.VECTOR2I(pcbnew.FromMM(DRILL),
                                         pcbnew.FromMM(DRILL)))
        ring = (PAD_D - DRILL) / 2
        changed += 1

    DST_LIB.mkdir(parents=True, exist_ok=True)
    fp.SetFPID(pcbnew.LIB_ID("x68k", DST))
    pcbnew.FootprintSave(str(DST_LIB), fp)
    print(f"{changed} pads -> {PAD_D} mm on a {DRILL} mm drill "
          f"(annular ring {ring:.2f} mm, JLCPCB minimum 0.13)")
    print(f"gap between neighbours: {2.54 - PAD_D:.2f} mm "
          f"(was {2.54 - 1.70:.2f}); two 0.15 mm tracks need 0.75")
    if changed != 40:
        print(f"EXPECTED 40 through-hole pads, changed {changed}")
        return 1
    print(f"wrote {DST_LIB / (DST + '.kicad_mod')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
