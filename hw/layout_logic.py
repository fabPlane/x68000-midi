"""Place the logic board: FPGA, memory, MIDI — the half behind the breakout.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/layout_logic.py

Same approach as `layout_breakout.py` and deliberately not shared code: the two
boards differ in every constant that matters, and a parameterised placer for two
boards would be harder to read than two placers.

Geometry. The card sits behind the breakout in the same slot: the slot takes
midiori's 141 mm, the breakout uses 69, so this board gets 70 × 150 mm with the
ribbons on its left edge (facing the breakout) and the MIDI jacks on its right
(at the slot plate). Height is fixed by the slot; only the depth was ours to
choose, and it is what is left over.

Flow, left to right: ribbons in, FPGA in the middle with its decoupling, MIDI
drivers and jacks out the back. The QFN-88's 0.4 mm pitch is the only part of
this board that is harder than the breakout, and it is freerouting's problem.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from logic import build  # noqa: E402
from netlist import renumber  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KICAD_FP = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints")
LOCAL_FP = ROOT / "hw" / "lib"

WIDTH, HEIGHT = 70.0, 150.0
CHAMFER = 3.0
OUTLINE = [(CHAMFER, 0), (WIDTH - CHAMFER, 0), (WIDTH, CHAMFER),
           (WIDTH, HEIGHT - CHAMFER), (WIDTH - CHAMFER, HEIGHT),
           (CHAMFER, HEIGHT), (0, HEIGHT - CHAMFER), (0, CHAMFER)]

#: ref -> (x, y, rotation, side).
#:
#: The ribbon headers are rotated 180 so their shrouds point -X, back toward
#: the breakout; at 0 they would fire the cable at the slot plate. Their pads
#: then run -Y from pad 1, which is why pad 1 sits high.
PLACEMENT = {
    "J1": (18.0, 128.0, 180, "F"),      # breakout J2: cycle + data
    "J2": (18.0, 66.0, 180, "F"),       # breakout J3: address
    "U1": (40.0, 75.0, 0, "F"),         # GW1NR-9, QFN-88
    "J6": (40.0, 95.0, 0, "F"),         # JTAG, close to the FPGA
    "U2": (56.0, 108.0, 0, "F"),        # MIDI OUT drivers
    "U3": (56.0, 60.0, 0, "F"),         # MIDI IN opto
    # MIDI jacks along the rear edge, 22 mm apart to match the cover plate.
    "J3": (64.0, 122.0, 270, "F"),      # OUT
    "J4": (64.0, 100.0, 270, "F"),      # OUT 2
    "J5": (64.0, 78.0, 270, "F"),       # IN (DNP)
    "U4": (30.0, 10.0, 0, "F"),         # 3.3 V — clear of the header column
    "U5": (40.0, 10.0, 0, "F"),         # 1.8 V
    "U6": (50.0, 10.0, 0, "F"),         # 1.2 V
    "X1": (50.0, 90.0, 0, "F"),         # 27 MHz, near its PLL pin
    "SW1": (46.0, 20.0, 0, "F"),
    "D2": (56.0, 20.0, 0, "F"),
}

FREE_AREA = (3.0, 3.0, WIDTH - 3.0, HEIGHT - 3.0)

#: Parts that are *supposed* to reach the board edge, so the free-area test
#: does not apply to them: a panel jack's socket has to be at the edge or it
#: cannot look out through the cover plate.
EDGE_PARTS = {"J3", "J4", "J5"}
GRID = 1.0
CLEARANCE = 0.4


def mm(v: float) -> int:
    return pcbnew.FromMM(v)


def point(x: float, y: float):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def load_footprint(spec: str):
    lib, name = spec.split(":")
    for root in (KICAD_FP, LOCAL_FP):
        if (root / f"{lib}.pretty" / f"{name}.kicad_mod").exists():
            return pcbnew.FootprintLoad(str(root / f"{lib}.pretty"), name)
    raise SystemExit(f"footprint {spec} not found")


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


def bbox_mm(fp):
    b = fp.GetBoundingBox(False, False)
    return (pcbnew.ToMM(b.GetLeft()), pcbnew.ToMM(b.GetTop()),
            pcbnew.ToMM(b.GetRight()), pcbnew.ToMM(b.GetBottom()))


def overlaps(a, b, gap=CLEARANCE) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0]
                or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def anchor_of(design, ref: str) -> tuple[float, float]:
    """Beside whatever placed part this one shares a net with — decoupling
    then lands next to the thing it decouples."""
    for net, nodes in design.nets.items():
        if net == "GND" or not any(r == ref for r, _ in nodes):
            continue
        for other, _ in nodes:
            if other in PLACEMENT:
                x, y, _, _ = PLACEMENT[other]
                return x, y + 8.0
    return (WIDTH / 2, 30.0)


def first_fit(fp, anchor, taken):
    x0, y0, x1, y1 = FREE_AREA
    ax, ay = anchor
    pts = []
    for i in range(int((x1 - x0) / GRID) + 1):
        for j in range(int((y1 - y0) / GRID) + 1):
            x, y = x0 + i * GRID, y0 + j * GRID
            pts.append(((x - ax) ** 2 + (y - ay) ** 2, x, y))
    pts.sort()
    for _, x, y in pts:
        fp.SetPosition(point(x, y))
        box = bbox_mm(fp)
        if box[0] < x0 or box[1] < y0 or box[2] > x1 or box[3] > y1:
            continue
        if any(overlaps(box, t) for t, side in taken if side == "F"):
            continue
        return x, y
    return None


def main() -> int:
    design, _ = build()
    renumber(design)

    board = pcbnew.BOARD()
    board.SetCopperLayerCount(4)
    ds = board.GetDesignSettings()
    ds.m_TrackMinWidth = mm(0.15)      # 0.4 mm pitch QFN escapes need it
    ds.m_MinClearance = mm(0.15)
    ds.m_ViasMinSize = mm(0.45)
    ds.m_MinThroughDrill = mm(0.25)

    nets = {}
    for name in sorted(design.nets):
        board.Add(pcbnew.NETINFO_ITEM(board, name))
        nets[name] = board.FindNet(name)
    pad_net = {}
    for name, nodes in design.nets.items():
        for ref, pad in nodes:
            pad_net[(ref, pad)] = name

    taken, unplaced, off_board, collisions, missing = [], [], [], [], []
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
        board.Add(fp)
        if side == "B":
            fp.SetLayerAndFlip(pcbnew.B_Cu)
        if ref not in PLACEMENT:
            spot = first_fit(fp, anchor_of(design, ref), taken)
            if spot is None:
                unplaced.append(ref)
                continue
            fp.SetPosition(point(*spot))
        box = bbox_mm(fp)
        x0, y0, x1, y1 = FREE_AREA
        if ref not in EDGE_PARTS and (box[0] < x0 or box[1] < y0
                                      or box[2] > x1 or box[3] > y1):
            off_board.append(f"{ref} {tuple(round(v, 1) for v in box)}")
        if ref in EDGE_PARTS and box[2] > WIDTH:
            off_board.append(f"{ref} hangs off the rear edge: {box[2]:.1f} > {WIDTH}")
        if any(overlaps(box, t) for t, s in taken if s == side):
            collisions.append(ref)
        taken.append((box, side))
        if comp["dnp"]:
            fp.SetDNP(True)
        have = {p.GetNumber() for p in fp.Pads()}
        for pad in fp.Pads():
            key = (ref, pad.GetNumber())
            if key in pad_net:
                pad.SetNet(nets[pad_net[key]])
        for (r, p) in pad_net:
            if r == ref and p not in have:
                missing.append(f"{ref} pad {p}")

    draw_outline(board)
    out = ROOT / "build"
    out.mkdir(exist_ok=True)
    path = out / "x68000-logic.kicad_pcb"
    pcbnew.SaveBoard(str(path), board)

    print(f"{len(design.comps)} footprints, {len(nets)} nets")
    print(f"board {WIDTH:.0f} x {HEIGHT:.0f} mm, 4 layers")
    for label, items in (("UNPLACED", unplaced), ("OUTSIDE THE BOARD", off_board),
                         ("OVERLAPPING", collisions),
                         ("PADS NOT ON THE FOOTPRINT", sorted(set(missing)))):
        if items:
            print(f"{label}: {', '.join(items)}")
    print(f"wrote {path}")
    return 1 if (unplaced or off_board or collisions or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
