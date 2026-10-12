"""The last three GND connections, placed where the generic passes would not.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/fix_last_gnd.py

Two jobs, both too specific for the heuristics in `route_breakout.py`:

* **A ground finger with nowhere obvious to go.** J1's pads are 8 mm long on a
  2.54 mm pitch, so the 0.74 mm gap between neighbours will never take a via.
  The room is east of the fingers, where the board widens from the tab into the
  body — so search there, over a wide area, and run a stub to it.
* **Pour islands.** The GND pours on F.Cu, In1 and B.Cu are three separate
  pieces of copper, and routing can cut each into islands. A stitching via only
  helps if it lands where *both* surface pours actually have copper, so test
  against the filled polygons rather than the zone outlines.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "build" / "x68000-breakout.kicad_pcb"

VIA_D, VIA_DRILL = 0.6, 0.3
STUB_W = 0.25
CLEARANCE = 0.3


def mm(v):
    return pcbnew.FromMM(v)


def to_mm(v):
    return pcbnew.ToMM(v)


def seg_distance(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5


def rect_distance(p, centre, half) -> float:
    """Point to axis-aligned rectangle. A gold finger is 8 x 1.8 mm; calling
    that a 4 mm circle (as the first version did) blocks every route east of
    the connector and is why nothing could be placed there."""
    dx = max(0.0, abs(p[0] - centre[0]) - half[0])
    dy = max(0.0, abs(p[1] - centre[1]) - half[1])
    return (dx * dx + dy * dy) ** 0.5


def segments_cross(a1, a2, b1, b2) -> bool:
    """True if the two segments actually intersect.

    The obvious test -- the smallest distance between the four endpoints and
    the opposite segment -- is wrong precisely where it matters: two segments
    crossing in an X have all four endpoints far from each other. That is what
    put seven `tracks_crossing` errors on the board.
    """
    def side(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (v > 1e-12) - (v < -1e-12)

    d1, d2 = side(b1, b2, a1), side(b1, b2, a2)
    d3, d4 = side(a1, a2, b1), side(a1, a2, b2)
    return d1 != d2 and d3 != d4


def copper(board):
    out = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            p = pad.GetPosition()
            half = (to_mm(pad.GetSize().x) / 2, to_mm(pad.GetSize().y) / 2)
            out.append(("rect", (to_mm(p.x), to_mm(p.y)), half,
                        pad.GetNet().GetNetname()))
    for t in board.Tracks():
        net = t.GetNet().GetNetname()
        if t.Type() == pcbnew.PCB_VIA_T:
            p = t.GetPosition()
            out.append(("rect", (to_mm(p.x), to_mm(p.y)),
                        (VIA_D / 2, VIA_D / 2), net))
        else:
            a, b = t.GetStart(), t.GetEnd()
            out.append(("seg", ((to_mm(a.x), to_mm(a.y)),
                                (to_mm(b.x), to_mm(b.y))),
                        to_mm(t.GetWidth()) / 2, net))
    return out


def via_fits(items, x, y, net) -> bool:
    r = VIA_D / 2 + CLEARANCE
    for kind, geom, size, onet in items:
        if kind == "rect":
            if onet == net:
                # Same net: only the hole-to-hole rule applies, and only to
                # the vias (a pad we are allowed to sit on top of).
                if size == (VIA_D / 2, VIA_D / 2) and \
                        rect_distance((x, y), geom, size) < VIA_DRILL + 0.5:
                    return False
            elif rect_distance((x, y), geom, size) < r:
                return False
        elif onet != net and seg_distance((x, y), *geom) < r + size:
            return False
    return True


def stub_fits(items, a, b, net) -> bool:
    r = STUB_W / 2 + CLEARANCE
    steps = max(2, int(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 / 0.2))
    points = [(a[0] + (b[0] - a[0]) * i / steps,
               a[1] + (b[1] - a[1]) * i / steps) for i in range(steps + 1)]
    for kind, geom, size, onet in items:
        if onet == net:
            continue
        if kind == "rect":
            if any(rect_distance(p, geom, size) < r for p in points):
                return False
        else:
            if segments_cross(a, b, *geom):
                return False
            if min(seg_distance(geom[0], a, b), seg_distance(geom[1], a, b),
                   seg_distance(a, *geom), seg_distance(b, *geom)) < r + size:
                return False
    return True


def add_via(board, net, x, y):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
    v.SetWidth(mm(VIA_D))
    v.SetDrill(mm(VIA_DRILL))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNet(net)
    board.Add(v)


def add_stub(board, net, layer, a, b):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
    t.SetEnd(pcbnew.VECTOR2I(mm(b[0]), mm(b[1])))
    t.SetWidth(mm(STUB_W))
    t.SetLayer(layer)
    t.SetNet(net)
    board.Add(t)


def connect_ground_fingers(board, items) -> list[str]:
    """Every J1 pad on GND gets a stub east into the body and a via."""
    done = []
    net = board.FindNet("GND")
    for fp in board.GetFootprints():
        if fp.GetReference() != "J1":
            continue
        for pad in fp.Pads():
            if pad.GetNet().GetNetname() != "GND":
                continue
            px, py = to_mm(pad.GetPosition().x), to_mm(pad.GetPosition().y)
            layer = pcbnew.F_Cu if pad.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
            # Start from the inboard end of the finger, not its centre.
            start = (px + to_mm(pad.GetSize().x) / 2 - 0.4, py)
            for x in [start[0] + d * 0.25 for d in range(4, 80)]:
                for y in (py, py - 0.5, py + 0.5, py - 1.0, py + 1.0):
                    if (via_fits(items, x, y, "GND")
                            and stub_fits(items, start, (x, y), "GND")):
                        add_stub(board, net, layer, start, (x, y))
                        add_via(board, net, x, y)
                        items.append(("rect", (x, y),
                                      (VIA_D / 2, VIA_D / 2), "GND"))
                        items.append(("seg", (start, (x, y)), STUB_W / 2, "GND"))
                        done.append(f"{pad.GetNumber()} -> via at "
                                    f"{x:.1f},{y:.1f}")
                        break
                else:
                    continue
                break
    return done


def stitch_pours(board, items, count=8) -> int:
    """Vias where the F.Cu and B.Cu GND pours both have copper."""
    net = board.FindNet("GND")
    surfaces = []
    for zone in board.Zones():
        if zone.GetNet().GetNetname() != "GND":
            continue
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            if zone.IsOnLayer(layer):
                polys = zone.GetFilledPolysList(layer)
                if polys:
                    surfaces.append((layer, polys))

    def in_pour(layer, x, y):
        p = pcbnew.VECTOR2I(mm(x), mm(y))
        return any(lay == layer and polys.Contains(p) for lay, polys in surfaces)

    made = 0
    y = 44.0
    while y < 186.0 and made < count:
        x = 60.0
        while x < 118.0 and made < count:
            if (in_pour(pcbnew.F_Cu, x, y) and in_pour(pcbnew.B_Cu, x, y)
                    and via_fits(items, x, y, "GND")):
                add_via(board, net, x, y)
                items.append(("rect", (x, y), (VIA_D / 2, VIA_D / 2), "GND"))
                made += 1
                x += 12.0
            else:
                x += 1.0
        y += 6.0
    return made


def main() -> int:
    board = pcbnew.LoadBoard(str(BOARD))
    items = copper(board)
    fingers = connect_ground_fingers(board, items)
    stitched = stitch_pours(board, items)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(BOARD), board)
    print(f"ground fingers: {len(fingers)} given a via")
    for line in fingers:
        print(f"  pad {line}")
    print(f"pour stitching: {stitched} vias where both surface pours have copper")
    return 0


if __name__ == "__main__":
    sys.exit(main())
