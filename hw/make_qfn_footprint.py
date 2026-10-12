"""Derive a QFN-88 land pattern a standard PCB process can actually make.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/make_qfn_footprint.py

KiCad ships `ArtInChip_QFN-88-1EP_10x10mm_P0.4mm_EP6.74x6.74mm`, whose pads are
0.30 mm wide on a 0.40 mm pitch. That leaves a **0.10 mm** copper gap between
neighbours — below JLCPCB's standard 0.127 mm capability, and below any sensible
board minimum, so every adjacent pad pair of different nets is a DRC clearance
error (84 of them) before a single track is routed.

The positions are right; only the width is aggressive. This narrows the pads to
0.22 mm, giving a 0.18 mm gap, and writes the result into the project's own
library. Pad length and the exposed pad are untouched.

**Check this against Gowin's package drawing before fab.** 0.22 mm is a normal
IPC land width for a 0.4 mm pitch QFN, but "normal" is not "verified", and the
part is the expensive one on the board.
"""

import sys
from pathlib import Path

import pcbnew

SRC_LIB = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
               "/Package_DFN_QFN.pretty")
SRC = "ArtInChip_QFN-88-1EP_10x10mm_P0.4mm_EP6.74x6.74mm"
DST_LIB = Path(__file__).resolve().parent / "lib" / "x68k.pretty"
DST = "QFN-88-1EP_10x10mm_P0.4mm_EP6.74x6.74mm_Standard"

PAD_W = 0.22          # was 0.30; 0.18 mm gap at 0.40 mm pitch


def main() -> int:
    fp = pcbnew.FootprintLoad(str(SRC_LIB), SRC)
    narrowed = 0
    for pad in fp.Pads():
        size = pad.GetSize()
        w, h = pcbnew.ToMM(size.x), pcbnew.ToMM(size.y)
        if max(w, h) > 2.0:
            continue                      # the exposed pad, left alone
        # The pitch runs along the pad's *short* axis, so that is the one to
        # thin. Getting this backwards shortens the pad, leaves the gap exactly
        # as it was, and changes nothing but the solder area -- which is what
        # the first version of this script did.
        if w > h:                         # long in x: neighbours are above and below
            pad.SetSize(pcbnew.VECTOR2I(size.x, pcbnew.FromMM(PAD_W)))
        else:                             # long in y: neighbours are left and right
            pad.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(PAD_W), size.y))
        narrowed += 1
    DST_LIB.mkdir(parents=True, exist_ok=True)
    fp.SetFPID(pcbnew.LIB_ID("x68k", DST))
    pcbnew.FootprintSave(str(DST_LIB), fp)
    print(f"narrowed {narrowed} pads to {PAD_W} mm, wrote {DST_LIB / (DST + '.kicad_mod')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
