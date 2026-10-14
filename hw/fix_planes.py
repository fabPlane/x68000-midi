"""Drop vias on the plane stubs the autorouter left hanging.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/fix_planes.py

Freerouting routes signals; it does not connect a pad to a plane it is sitting
over. The pads my stitcher could not reach before routing (no room) and the
stubs freerouting left behind therefore show up as unconnected GND, +5 V and
VREF items — never signals.

The fix is driven by DRC's own report rather than by guessing: for each
unconnected item on a plane net, walk the track it belongs to and drop a via at
the first point with room for one. A via on an existing track adds no new
copper that could collide with a neighbour, which is why this is safe to do
after routing when a stub track would not be.

Run the board's build script first; this reads its DRC report and edits its
`.kicad_pcb` in place. Which board is the one argument:

    hw/fix_planes.py            # the breakout, as before
    hw/fix_planes.py logic      # the logic board
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout_breakout import RIGHT  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: Per board: the file, its DRC report, the nets that are pours rather than
#: routed nets, where to pour surface ground, and the via size its netclass
#: uses. +1.8 V and +1.2 V are deliberately absent from the logic board's set:
#: they are routed nets there, so an unconnected one is a routing failure to
#: look at, not a stub to via down.
BOARDS = {
    "breakout": dict(
        board="x68000-breakout.kicad_pcb", report="drc-all.json",
        planes={"GND", "+5V", "VREF"}, via=(0.6, 0.3), ground=None),
    "logic": dict(
        board="x68000-logic.kicad_pcb", report="drc-logic.json",
        planes={"GND", "+3V3"}, via=(0.45, 0.25),
        ground=[(2.5, 2.5), (67.5, 2.5), (67.5, 147.5), (2.5, 147.5)]),
}

BOARD = ROOT / "build" / "x68000-breakout.kicad_pcb"
REPORT = ROOT / "build" / "drc-all.json"
PLANE_NETS = {"GND", "+5V", "VREF"}

#: SWIG hands the zone a raw pointer to its polygon; letting Python collect it
#: leaves a dangling reference and the next save segfaults.
_KEEPALIVE: list = []
VIA_D, VIA_DRILL = 0.6, 0.3
CLEARANCE = 0.2             # on top of the via's own radius
HOLE_TO_HOLE = 0.3          # board setting is 0.25; leave a little over
STEP = 0.25                 # how finely to walk a track looking for room


def to_mm(v):
    return pcbnew.ToMM(v)


def mm(v):
    return pcbnew.FromMM(v)


def seg_distance(p, a, b) -> float:
    """Distance from point p to segment ab, all (x, y) in mm."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    qx, qy = ax + t * dx, ay + t * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


class Obstacles:
    """Everything a new via has to stay clear of, with its net."""

    def __init__(self, board):
        self.pads = []
        self.segments = []
        self.vias = []
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                b = pad.GetBoundingBox()
                drill = to_mm(pad.GetDrillSize().x) / 2
                self.pads.append((to_mm(b.GetLeft()), to_mm(b.GetTop()),
                                  to_mm(b.GetRight()), to_mm(b.GetBottom()),
                                  pad.GetNet().GetNetname(),
                                  to_mm(pad.GetPosition().x),
                                  to_mm(pad.GetPosition().y), drill))
        for t in board.Tracks():
            net = t.GetNet().GetNetname()
            if t.Type() == pcbnew.PCB_VIA_T:
                p = t.GetPosition()
                self.vias.append((to_mm(p.x), to_mm(p.y),
                                  to_mm(t.GetWidth()) / 2, net))
            else:
                a, b = t.GetStart(), t.GetEnd()
                self.segments.append(((to_mm(a.x), to_mm(a.y)),
                                      (to_mm(b.x), to_mm(b.y)),
                                      to_mm(t.GetWidth()) / 2, net))

    def room_for_via(self, x, y, net) -> bool:
        """A via here clears everything that is not on `net`.

        Pads are rectangles. Modelling them as circles of radius max(w, h)/2
        understates a square pad's corner by up to 41% of its half-width, and
        that is how a stitching via ended up 0.09 mm from the ribbon's 3.3 V
        pin and another 0.145 mm from a 0603 ground pad -- both inside the
        0.15 mm rule, both reported by DRC, neither seen by this check.
        """
        r = VIA_D / 2 + CLEARANCE
        for x0, y0, x1, y1, pnet, cxp, cyp, drill in self.pads:
            # A plated hole needs hole-to-hole spacing whatever the net.
            if drill > 0:
                gap = ((cxp - x) ** 2 + (cyp - y) ** 2) ** 0.5
                if gap < drill + VIA_DRILL / 2 + HOLE_TO_HOLE:
                    return False
            if pnet == net:
                continue
            dx = max(x0 - x, 0.0, x - x1)
            dy = max(y0 - y, 0.0, y - y1)
            if (dx * dx + dy * dy) ** 0.5 < r:
                return False
        for vx, vy, size, vnet in self.vias:
            # Hole-to-hole applies whatever the net: dropping a via on top of
            # an existing one of the same net is electrically harmless and
            # mechanically nonsense, and KiCad reports it as co-located holes.
            limit = r + size if vnet != net else VIA_DRILL + 0.5
            if (vx - x) ** 2 + (vy - y) ** 2 < limit ** 2:
                return False
        for a, b, half, snet in self.segments:
            if snet == net:
                continue
            if seg_distance((x, y), a, b) < r + half:
                return False
        return True

    def note_via(self, x, y, net):
        self.vias.append((x, y, VIA_D / 2, net))


def net_of(description: str) -> str | None:
    m = re.search(r"\[([^\]]+)\]", description)
    return m.group(1) if m else None


def tracks_near(board, net, pos, radius=3.0):
    """Tracks of `net` with a point within `radius` of pos."""
    out = []
    for t in board.Tracks():
        if t.Type() == pcbnew.PCB_VIA_T or t.GetNet().GetNetname() != net:
            continue
        a = (to_mm(t.GetStart().x), to_mm(t.GetStart().y))
        b = (to_mm(t.GetEnd().x), to_mm(t.GetEnd().y))
        if seg_distance(pos, a, b) <= radius:
            out.append((t, a, b))
    return out


def walk(a, b):
    """Sample points along a segment, middle outwards."""
    length = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
    n = max(1, int(length / STEP))
    order = sorted(range(n + 1), key=lambda i: abs(i - n / 2))
    for i in order:
        t = i / n
        yield a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def add_via(board, net, x, y):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
    v.SetWidth(mm(VIA_D))
    v.SetDrill(mm(VIA_DRILL))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNet(net)
    board.Add(v)
    return v


#: The body only, deliberately not the finger tab: between gold fingers at
#: 2.54 mm pitch a surface pour is a row of slivers that fills as isolated
#: islands, which DRC then reports as unconnected GND zones. The tab keeps its
#: inner-layer pour, which is what the ground fingers actually need.
GROUND_OUTLINE = [(58, 41), (RIGHT - 1.0, 41), (RIGHT - 1.0, 187), (58, 187)]


def add_surface_ground(board) -> None:
    """Pour GND into the leftover surface copper on both outer layers.

    Standard practice, and it does most of this script's job for free: a GND
    pad on F.Cu reaches the pour on its own layer with no via at all, which is
    why the edge connector's ground fingers stop being a problem.
    """
    net = board.FindNet("GND")
    already = {z.GetLayer() for z in board.Zones()
               if z.GetNet().GetNetname() == "GND"}
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        if layer in already:
            continue          # re-running must not stack pours on each other
        zone = pcbnew.ZONE(board)
        zone.SetLayer(layer)
        zone.SetNetCode(net.GetNetCode())
        zone.SetLocalClearance(mm(0.25))
        # 0.5 mm, not 0.2: a surface pour is here for stitching, not current,
        # and a 0.2 mm minimum lets it fill 0.7 mm slivers between routed
        # tracks that are too narrow to take a via and so can never reach the
        # plane. Not filling them is better than reporting them.
        zone.SetMinThickness(mm(0.5))
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        # Drop fills that end up isolated. A surface pour threaded between
        # routed tracks always leaves slivers with no path back to the plane,
        # and DRC is right to call them unconnected copper: the answer is for
        # them not to exist, not to spend a via on each.
        zone.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        outline = pcbnew.SHAPE_POLY_SET()
        outline.NewOutline()
        for x, y in GROUND_OUTLINE:
            outline.Append(mm(x), mm(y))
        zone.SetOutline(outline)
        _KEEPALIVE.append(outline)
        zone.SetIsFilled(False)
        board.Add(zone)


def select(name: str) -> None:
    """Point the module's globals at one board. They are globals because
    `Obstacles` and `add_via` read them directly; threading a config object
    through every helper would be a bigger change than this file deserves."""
    global BOARD, REPORT, PLANE_NETS, VIA_D, VIA_DRILL, GROUND_OUTLINE
    try:
        cfg = BOARDS[name]
    except KeyError:
        raise SystemExit(f"unknown board {name!r}; "
                         f"expected one of {', '.join(sorted(BOARDS))}")
    BOARD = ROOT / "build" / cfg["board"]
    REPORT = ROOT / "build" / cfg["report"]
    PLANE_NETS = cfg["planes"]
    VIA_D, VIA_DRILL = cfg["via"]
    if cfg["ground"] is not None:
        GROUND_OUTLINE = cfg["ground"]


def stitch_pour_regions(board, obstacles) -> int:
    """Give every filled region of the surface ground pour a via.

    A pour threaded between routed tracks fills as many separate regions. One
    that holds a pad is not an "island" -- KiCad keeps it, correctly, because
    it is connected to something -- but with no via it never reaches the
    ground plane, and DRC reports it as unconnected copper. One via per region
    is the fix; regions too small to take one are left to the min-thickness
    rule above.
    """
    net = board.FindNet("GND")
    made = 0
    for zone in board.Zones():
        if (zone.GetNetname() != "GND"
                or zone.GetLayer() not in (pcbnew.F_Cu, pcbnew.B_Cu)):
            continue
        poly = zone.GetFilledPolysList(zone.GetLayer())
        for i in range(poly.OutlineCount()):
            out = poly.Outline(i)
            pts = [(to_mm(out.CPoint(k).x), to_mm(out.CPoint(k).y))
                   for k in range(out.PointCount())]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            # Does this region already reach the plane? Polygon containment,
            # not the bounding box: the board-sized outer region's box covers
            # every other region, so a box test says yes to all of them.
            if any(poly.Contains(pcbnew.VECTOR2I(mm(vx), mm(vy)), i)
                   for vx, vy, _, vnet in obstacles.vias if vnet == "GND"):
                continue
            placed = False
            steps = 24
            for a in range(steps + 1):
                for b in range(steps + 1):
                    x = min(xs) + (max(xs) - min(xs)) * a / steps
                    y = min(ys) + (max(ys) - min(ys)) * b / steps
                    if not poly.Contains(pcbnew.VECTOR2I(mm(x), mm(y)), i):
                        continue
                    if not obstacles.room_for_via(x, y, "GND"):
                        continue
                    add_via(board, net, x, y)
                    obstacles.note_via(x, y, "GND")
                    made += 1
                    placed = True
                    break
                if placed:
                    break
    return made


def main() -> int:
    select(sys.argv[1] if len(sys.argv) > 1 else "breakout")
    if not REPORT.exists():
        raise SystemExit(f"{REPORT} not found — run that board's build first")
    board = pcbnew.LoadBoard(str(BOARD))
    obstacles = Obstacles(board)
    report = json.loads(REPORT.read_text())

    # One via per unconnected pair, placed on whichever end is a track: a via
    # on existing copper adds nothing that can collide with a neighbour.
    wanted = []
    for entry in report.get("unconnected_items", []):
        items = entry.get("items", [])
        items.sort(key=lambda i: 0 if i.get("description", "").startswith("Track")
                   else 1)
        for item in items:
            net = net_of(item.get("description", ""))
            if net in PLANE_NETS:
                wanted.append((net, (item["pos"]["x"], item["pos"]["y"])))
                break

    added, failed = 0, []
    for net_name, pos in wanted:
        net = board.FindNet(net_name)
        placed = False
        for _, a, b in tracks_near(board, net_name, pos):
            for x, y in walk(a, b):
                if obstacles.room_for_via(x, y, net_name):
                    add_via(board, net, x, y)
                    obstacles.note_via(x, y, net_name)
                    added += 1
                    placed = True
                    break
            if placed:
                break
        if not placed:
            failed.append(f"{net_name} at {pos[0]:.1f},{pos[1]:.1f}")

    add_surface_ground(board)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    stitched = stitch_pour_regions(board, obstacles)
    if stitched:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(BOARD), board)
    print(f"{len(wanted)} plane items to connect, {added} vias added")
    print(f"surface pour: {stitched} region(s) given a via to the plane")
    if failed:
        print("no room for a via on: " + "; ".join(failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
