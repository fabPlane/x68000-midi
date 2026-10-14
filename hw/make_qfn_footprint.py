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

**Checked against Gowin UG119-1.6E Figure 4-1 (QN88/QN88P) on 2026-10-14**, a
copy of which Sipeed ship with the Tang Nano 9K (Gowin's own CDN 403s):

    b  terminal width    0.15 / 0.20 / 0.25      land here 0.22
    L  terminal length   0.30 / 0.40 / 0.50      land here 0.55
    e  pitch             0.40 BSC                land here 0.400
    D2 exposed pad       6.64 / 6.74 / 6.84      land here 6.74
    Nd span              8.40 REF (21 x 0.4)     land here 8.40, 22 per side

The terminal sits 4.600-5.000 mm from the package centre; this land spans
4.525-5.075, so it covers the terminal with 0.075 mm proud at each end. That
toe extension is on the short side of what IPC-7351 would give a QFN, which
costs a little solder-fillet visibility and nothing else -- if reflow yield
ever disappoints, lengthening the land outwards to ~0.75 mm is the first thing
to try. Note UG119 gives package outlines only; it contains no recommended
land pattern for any package, so the land is derived, not quoted.

Only pads 1-88 are touched. The source footprint also carries nine *unnumbered,
paste-only* apertures over the exposed pad -- 1.81 mm squares that between them
cover about 65% of it, which is how a QFN's thermal pad is stencilled. An
earlier version of this script narrowed those too, because at 1.81 mm they slip
under a "bigger than 2 mm is the exposed pad" test, and that cut paste coverage
to 8%: the ground pad under the FPGA would have had almost no solder.
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
#: The perimeter pads, and nothing else: not the exposed pad, not its stencil
#: apertures, which are unnumbered.
PERIMETER = {str(n) for n in range(1, 89)}


def main() -> int:
    fp = pcbnew.FootprintLoad(str(SRC_LIB), SRC)
    narrowed, skipped = 0, 0
    for pad in fp.Pads():
        size = pad.GetSize()
        if pad.GetNumber() not in PERIMETER:
            skipped += 1                 # exposed pad and its paste apertures
            continue
        w, h = pcbnew.ToMM(size.x), pcbnew.ToMM(size.y)
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
    print(f"narrowed {narrowed} pads to {PAD_W} mm, left {skipped} alone "
          f"(exposed pad + stencil apertures)")
    if narrowed != 88:
        print(f"EXPECTED 88 perimeter pads, narrowed {narrowed}")
        return 1
    print(f"wrote {DST_LIB / (DST + '.kicad_mod')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
