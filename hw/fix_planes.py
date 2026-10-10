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

Run `hw/build_breakout.sh` first; this reads `build/drc-all.json` and edits
`build/x68000-breakout.kicad_pcb` in place.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "build" / "x68000-breakout.kicad_pcb"
REPORT = ROOT / "build" / "drc-all.json"

PLANE_NETS = {"GND", "+5V", "VREF"}

#: SWIG hands the zone a raw pointer to its polygon; letting Python collect it
#: leaves a dangling reference and the next save segfaults.
_KEEPALIVE: list = []
VIA_D, VIA_DRILL = 0.6, 0.3
CLEARANCE = 0.2             # on top of the via's own radius
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
                p = pad.GetPosition()
                size = max(to_mm(pad.GetSize().x), to_mm(pad.GetSize().y)) / 2
                self.pads.append((to_mm(p.x), to_mm(p.y), size,
                                  pad.GetNet().GetNetname()))
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
        """A via here clears everything that is not on `net`."""
        r = VIA_D / 2 + CLEARANCE
        for px, py, size, pnet in self.pads:
            if pnet == net:
                continue
            if (px - x) ** 2 + (py - y) ** 2 < (r + size) ** 2:
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


#: The same outline the In1 pour uses, so the surface copper lines up with it.
GROUND_OUTLINE = [(46, 48), (58, 48), (58, 41), (119, 41),
                  (119, 187), (58, 187), (58, 174), (46, 174)]


def add_surface_ground(board) -> None:
    """Pour GND into the leftover surface copper on both outer layers.

    Standard practice, and it does most of this script's job for free: a GND
    pad on F.Cu reaches the pour on its own layer with no via at all, which is
    why the edge connector's ground fingers stop being a problem.
    """
    net = board.FindNet("GND")
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        zone = pcbnew.ZONE(board)
        zone.SetLayer(layer)
        zone.SetNetCode(net.GetNetCode())
        zone.SetLocalClearance(mm(0.25))
        zone.SetMinThickness(mm(0.2))
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        outline = pcbnew.SHAPE_POLY_SET()
        outline.NewOutline()
        for x, y in GROUND_OUTLINE:
            outline.Append(mm(x), mm(y))
        zone.SetOutline(outline)
        _KEEPALIVE.append(outline)
        zone.SetIsFilled(False)
        board.Add(zone)


def main() -> int:
    if not REPORT.exists():
        raise SystemExit(f"{REPORT} not found — run hw/build_breakout.sh first")
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
    pcbnew.SaveBoard(str(BOARD), board)
    print(f"{len(wanted)} plane items to connect, {added} vias added")
    if failed:
        print("no room for a via on: " + "; ".join(failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
