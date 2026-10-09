"""Place the slot breakout: board outline, footprints, nets. No tracks.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/layout_breakout.py

Built through KiCad's own ``pcbnew`` API rather than by writing S-expressions,
so the file is valid by construction. Output is ``build/x68000-breakout.kicad_pcb``
with every footprint placed and every pad on its net, ready to route by hand or
with freerouting.

The geometry of the finger end is copied from midiori's board, because that is
the part that has to fit a real slot and we have no machine to measure:

    tab x 44..54, y 47..175, 1 mm lead-in chamfers; steps out to x 58; the body
    runs y 39..189 (150 mm, fixed by the slot) with 4 mm corner chamfers.

Only the depth is ours: 76 mm instead of midiori's 141 mm, because a breakout
holds six translators and two headers and nothing else. The card is still held
by the fingers at one end and the plate at the other.

Placement follows the signal flow, left to right: fingers, a column of
translators with their A side facing the fingers and their B side facing the
headers, then the two ribbons. Everything is on the top layer so the board can
be assembled single-sided, which means the B-row fingers (the address bus) vias
up — unavoidable on a connector with pads on both faces.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from breakout import build  # noqa: E402
from netlist import renumber  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KICAD_FP = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints")
LOCAL_FP = ROOT / "hw" / "lib"

# --- board outline, in mm, midiori's frame --------------------------------
RIGHT = 120.0           # plate end; midiori's is 185
OUTLINE = [
    (44, 48), (45, 47), (54, 47), (54, 43), (58, 39),
    (RIGHT - 4, 39), (RIGHT, 43), (RIGHT, 185), (RIGHT - 4, 189),
    (58, 189), (54, 185), (54, 175), (45, 175), (44, 174),
]

#: ref -> (x, y, rotation, side). Chosen by hand; the router may move things,
#: which is fine — this only has to be a sane starting point.
#:
#: The one decision here that matters: the **address translators go on the
#: bottom**. The edge connector's A row is on F.Cu and its B row on B.Cu, so
#: with the data and control parts on top and the address parts underneath,
#: the two big bundles never share a layer and cannot interfere. That is worth
#: the cost of double-sided assembly; routed as one-sided it is a weave.
#: Both columns sit at the same x, because opposite sides do not collide.
PLACEMENT = {
    "J1": (50.0, 173.46, 0, "F"),     # exactly where midiori puts the fingers
    # data and control, top side, at the centre of gravity of their fingers
    "U1": (70.0, 156.0, 0, "F"),      # SD0-7   <- A4-A12
    "U2": (70.0, 134.0, 0, "F"),      # SD8-15  <- A13-A20
    "U6": (70.0, 95.0, 0, "F"),       # strobes <- A27-A30, A37, B30, B44/45
    # address, bottom side, under the others
    "U3": (70.0, 152.0, 0, "B"),      # SA1-8   <- B5-B13
    "U4": (70.0, 129.0, 0, "B"),      # SA9-16  <- B14-B22
    "U5": (70.0, 107.0, 0, "B"),      # SA17-23 <- B23-B29, CLK10M <- B2
    "U7": (60.0, 146.0, 0, "F"),      # AHCT direction buffer
    "U8": (62.0, 70.0, 0, "F"),       # open-drain returns, near B42-B45
    "U9": (62.0, 62.0, 0, "F"),
    "U10": (66.0, 176.0, 0, "F"),     # local 3.3 V LDO (DNP)
    "JP1": (70.0, 182.0, 0, "F"),
    "F1": (59.0, 182.0, 90, "F"),
    # Ribbons. This footprint's origin is pad 1 and its 20 rows run +Y from
    # there, so rotation 0 already has the long axis down the card. They are
    # through-hole, so either side can reach them.
    "J2": (96.0, 48.0, 0, "F"),
    "J3": (96.0, 112.0, 0, "F"),
}

#: Everything not named above is placed by first fit: candidate points on a
#: 1 mm grid inside this rectangle, nearest to the part's anchor first, taking
#: the first that does not overlap something already down. Crude, but it is
#: checkable — the DRC run at the end is the test.
FREE_AREA = (57.0, 41.0, RIGHT - 2.0, 187.0)
GRID = 1.0
CLEARANCE = 0.4


def load_footprint(spec: str):
    lib, name = spec.split(":")
    for root in (KICAD_FP, LOCAL_FP):
        path = root / f"{lib}.pretty"
        if (path / f"{name}.kicad_mod").exists():
            return pcbnew.FootprintLoad(str(path), name)
    raise SystemExit(f"footprint {spec} not found")


def mm(v: float) -> int:
    return pcbnew.FromMM(v)


def point(x: float, y: float):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def draw_outline(board) -> None:
    pts = OUTLINE + [OUTLINE[0]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(point(x0, y0))
        seg.SetEnd(point(x1, y1))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(mm(0.1))
        board.Add(seg)


def anchor_of(design, ref: str) -> tuple[float, float]:
    """Where this part would like to be: beside whichever placed part it
    shares a net with. Decoupling then lands next to what it decouples."""
    for net, nodes in design.nets.items():
        if net == "GND" or not any(r == ref for r, _ in nodes):
            continue
        for other, _ in nodes:
            if other in PLACEMENT:
                x, y = PLACEMENT[other][0], PLACEMENT[other][1]
                return x - 8.0, y
    return (RIGHT - 10.0, 180.0)


def bbox_mm(fp) -> tuple[float, float, float, float]:
    b = fp.GetBoundingBox(False, False)
    return (pcbnew.ToMM(b.GetLeft()), pcbnew.ToMM(b.GetTop()),
            pcbnew.ToMM(b.GetRight()), pcbnew.ToMM(b.GetBottom()))


def overlaps(a, b, gap=CLEARANCE) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0]
                or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def first_fit(fp, anchor, taken) -> tuple[float, float] | None:
    """Try grid points nearest the anchor first."""
    x0, y0, x1, y1 = FREE_AREA
    ax, ay = anchor
    candidates = []
    n_x = int((x1 - x0) / GRID)
    n_y = int((y1 - y0) / GRID)
    for i in range(n_x + 1):
        for j in range(n_y + 1):
            x, y = x0 + i * GRID, y0 + j * GRID
            candidates.append(((x - ax) ** 2 + (y - ay) ** 2, x, y))
    candidates.sort()
    for _, x, y in candidates:
        fp.SetPosition(point(x, y))
        box = bbox_mm(fp)
        if box[0] < x0 or box[1] < y0 or box[2] > x1 or box[3] > y1:
            continue
        if any(overlaps(box, t) for t, side in taken if side == "F"):
            continue
        return x, y
    return None


def main() -> int:
    design = build()
    renumber(design)

    board = pcbnew.BOARD()
    board.SetCopperLayerCount(4)

    nets = {}
    for name in sorted(design.nets):
        item = pcbnew.NETINFO_ITEM(board, name)
        board.Add(item)
        nets[name] = board.FindNet(name)

    pad_net = {}
    for name, nodes in design.nets.items():
        for ref, pad in nodes:
            pad_net[(ref, pad)] = name

    unplaced, missing_pads, taken = [], [], []
    off_board, collisions = [], []
    # Hand-placed parts first, then first-fit the rest around them.
    order = ([r for r in sorted(design.comps) if r in PLACEMENT]
             + [r for r in sorted(design.comps) if r not in PLACEMENT])
    for ref in order:
        comp = design.comps[ref]
        fp = load_footprint(comp["footprint"])
        fp.SetReference(ref)
        fp.SetValue(comp["value"])
        fp.Reference().SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
        fp.Reference().SetTextThickness(mm(0.12))
        fp.Value().SetVisible(False)
        if ref in PLACEMENT:
            x, y, rot, side = PLACEMENT[ref]
            fp.SetOrientationDegrees(rot)
            fp.SetPosition(point(x, y))
        else:
            side = "F"
            fp.SetOrientationDegrees(0)
            spot = first_fit(fp, anchor_of(design, ref), taken)
            if spot is None:
                unplaced.append(ref)
                continue
            fp.SetPosition(point(*spot))
        # Flipping only works once the footprint belongs to a board.
        board.Add(fp)
        if side == "B":
            fp.SetLayerAndFlip(pcbnew.B_Cu)
        box = bbox_mm(fp)
        # Hand-placed parts get the same scrutiny as the auto-placed ones:
        # it is far too easy to rotate a 50 mm header off the end of a card.
        if ref != "J1":
            x0, y0, x1, y1 = FREE_AREA
            if box[0] < x0 or box[1] < y0 or box[2] > x1 or box[3] > y1:
                off_board.append(f"{ref} {tuple(round(v, 1) for v in box)}")
            if any(overlaps(box, t) for t, s in taken if s == side):
                collisions.append(ref)
        taken.append((box, side))
        if comp["dnp"]:
            fp.SetDNP(True)
        for pad in fp.Pads():
            key = (ref, pad.GetNumber())
            if key in pad_net:
                pad.SetNet(nets[pad_net[key]])
        have = {p.GetNumber() for p in fp.Pads()}
        for (r, p) in pad_net:
            if r == ref and p not in have:
                missing_pads.append(f"{ref} pad {p}")

    draw_outline(board)

    out = ROOT / "build"
    out.mkdir(exist_ok=True)
    path = out / "x68000-breakout.kicad_pcb"
    pcbnew.SaveBoard(str(path), board)

    print(f"{len(design.comps)} footprints, {len(nets)} nets")
    print(f"board {RIGHT - 44:.0f} x 150 mm, 4 layers")
    if unplaced:
        print(f"UNPLACED: {', '.join(unplaced)}")
    if missing_pads:
        print(f"PADS NOT ON THE FOOTPRINT: {', '.join(sorted(set(missing_pads)))}")
    if off_board:
        print(f"OUTSIDE THE BOARD: {', '.join(off_board)}")
    if collisions:
        print(f"OVERLAPPING: {', '.join(collisions)}")
    print(f"wrote {path}")
    return 1 if (unplaced or missing_pads or off_board or collisions) else 0


if __name__ == "__main__":
    sys.exit(main())
