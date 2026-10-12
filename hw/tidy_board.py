"""Close the last plane connections and clear the silkscreen.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/tidy_board.py

Runs after `hw/build_breakout.sh`. Two jobs it does better than a human would
enjoy doing by hand, and both are checkable by re-running DRC:

* **Plane stubs.** `fix_planes.py` only drops a via where one already fits on
  existing copper. The pads it gives up on are the crowded ones — a translator
  supply pin with routing on three sides. Here we also allow a short stub
  track to reach a clear spot, testing both the via and the stub against every
  other net's copper.
* **Silkscreen.** Reference text lands on pads and on other text. Passives get
  their references hidden, which is what a machine-assembled board does anyway;
  everything a human has to identify — ICs, connectors, the DIP switch, the
  fuse — keeps its reference and gets nudged to a clear spot instead.
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
VIA_D, VIA_DRILL = 0.6, 0.3
CLEARANCE = 0.35   # generous: a stub that crosses a track is worse than a stub that is never placed
STUB_W = 0.25
SEARCH_R, SEARCH_STEP = 5.0, 0.25

#: References that stay on the silkscreen: anything a person has to find.
KEEP_SILK = re.compile(r"^(U|J|SW|F|X|TP|JP|D)\d")


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


def segments_cross(a1, a2, b1, b2) -> bool:
    """True if the two segments actually intersect. Distance between endpoints
    does not detect an X crossing -- all four endpoints are far apart."""
    def side(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (v > 1e-12) - (v < -1e-12)

    d1, d2 = side(b1, b2, a1), side(b1, b2, a2)
    d3, d4 = side(a1, a2, b1), side(a1, a2, b2)
    return d1 != d2 and d3 != d4


def seg_seg_distance(a1, a2, b1, b2) -> float:
    if segments_cross(a1, a2, b1, b2):
        return 0.0
    return min(seg_distance(a1, b1, b2), seg_distance(a2, b1, b2),
               seg_distance(b1, a1, a2), seg_distance(b2, a1, a2))


class Copper:
    """Pads, tracks and vias, with their nets, for clearance testing."""

    def __init__(self, board):
        self.pads, self.segments, self.vias = [], [], []
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                p = pad.GetPosition()
                r = max(to_mm(pad.GetSize().x), to_mm(pad.GetSize().y)) / 2
                self.pads.append((to_mm(p.x), to_mm(p.y), r,
                                  pad.GetNet().GetNetname()))
        for t in board.Tracks():
            net = t.GetNet().GetNetname()
            if t.Type() == pcbnew.PCB_VIA_T:
                p = t.GetPosition()
                self.vias.append((to_mm(p.x), to_mm(p.y), VIA_D / 2, net))
            else:
                a, b = t.GetStart(), t.GetEnd()
                self.segments.append(((to_mm(a.x), to_mm(a.y)),
                                      (to_mm(b.x), to_mm(b.y)),
                                      to_mm(t.GetWidth()) / 2, net))

    def via_fits(self, x, y, net) -> bool:
        r = VIA_D / 2 + CLEARANCE
        for px, py, size, pnet in self.pads:
            if pnet != net and (px - x) ** 2 + (py - y) ** 2 < (r + size) ** 2:
                return False
        for vx, vy, size, vnet in self.vias:
            limit = r + size if vnet != net else VIA_DRILL + 0.5
            if (vx - x) ** 2 + (vy - y) ** 2 < limit ** 2:
                return False
        for a, b, half, snet in self.segments:
            if snet != net and seg_distance((x, y), a, b) < r + half:
                return False
        return True

    def stub_fits(self, a, b, net) -> bool:
        r = STUB_W / 2 + CLEARANCE
        for px, py, size, pnet in self.pads:
            if pnet != net and seg_distance((px, py), a, b) < r + size:
                return False
        for vx, vy, size, vnet in self.vias:
            if vnet != net and seg_distance((vx, vy), a, b) < r + size:
                return False
        for s, e, half, snet in self.segments:
            if snet != net and seg_seg_distance(a, b, s, e) < r + half:
                return False
        return True

    def note(self, kind, *args):
        (self.vias if kind == "via" else self.segments).append(args)


def spiral(cx, cy):
    """Grid points around (cx, cy), nearest first."""
    n = int(SEARCH_R / SEARCH_STEP)
    pts = [(i * SEARCH_STEP, j * SEARCH_STEP)
           for i in range(-n, n + 1) for j in range(-n, n + 1)]
    pts.sort(key=lambda d: d[0] ** 2 + d[1] ** 2)
    for dx, dy in pts:
        if 0.5 <= (dx * dx + dy * dy) ** 0.5 <= SEARCH_R:
            yield cx + dx, cy + dy


def connect_plane_stubs(board, copper) -> tuple[int, list[str]]:
    report = json.loads(REPORT.read_text())
    targets = []
    for entry in report.get("unconnected_items", []):
        for item in entry.get("items", []):
            net = re.search(r"\[([^\]]+)\]", item.get("description", ""))
            if net and net.group(1) in PLANE_NETS:
                targets.append((net.group(1), item["pos"]["x"], item["pos"]["y"],
                                item["description"]))
                break

    added, failed = 0, []
    for net_name, ax, ay, desc in targets:
        net = board.FindNet(net_name)
        layer = pcbnew.B_Cu if " on B.Cu" in desc else pcbnew.F_Cu
        placed = False
        for vx, vy in spiral(ax, ay):
            if not copper.via_fits(vx, vy, net_name):
                continue
            if not copper.stub_fits((ax, ay), (vx, vy), net_name):
                continue
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pcbnew.VECTOR2I(mm(ax), mm(ay)))
            t.SetEnd(pcbnew.VECTOR2I(mm(vx), mm(vy)))
            t.SetWidth(mm(STUB_W))
            t.SetLayer(layer)
            t.SetNet(net)
            board.Add(t)
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pcbnew.VECTOR2I(mm(vx), mm(vy)))
            v.SetWidth(mm(VIA_D))
            v.SetDrill(mm(VIA_DRILL))
            v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetNet(net)
            board.Add(v)
            copper.note("via", vx, vy, VIA_D / 2, net_name)
            copper.note("seg", (ax, ay), (vx, vy), STUB_W / 2, net_name)
            added += 1
            placed = True
            break
        if not placed:
            failed.append(f"{net_name} at {ax:.1f},{ay:.1f}")
    return added, failed


def tidy_silkscreen(board) -> tuple[int, int]:
    """Hide passive references, nudge the rest clear of their own pads."""
    hidden = moved = 0
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        text = fp.Reference()
        if not KEEP_SILK.match(ref):
            text.SetVisible(False)
            hidden += 1
            continue
        # Put the reference above the part, outside its own pad field.
        box = fp.GetBoundingBox(False, False)
        height = to_mm(box.GetHeight())
        text.SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
        text.SetTextThickness(mm(0.12))
        pos = fp.GetPosition()
        text.SetPosition(pcbnew.VECTOR2I(pos.x, pos.y - mm(height / 2 + 0.9)))
        text.SetTextAngle(pcbnew.EDA_ANGLE(0, pcbnew.DEGREES_T))
        moved += 1
    return hidden, moved


def drop_unused_anchors(board) -> int:
    """Remove plane vias no track ever reached.

    The anchors exist to give the router somewhere to aim; the ones it did not
    use are holes nobody pays for twice.

    GND anchors stay, used or not. They are not spare targets, they are the
    stitching that ties the GND pours on F.Cu, In1 and B.Cu into one piece —
    deleting them split the ground into islands that DRC then reported as
    unconnected zones.
    """
    ends = []
    for t in board.Tracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        for p in (t.GetStart(), t.GetEnd()):
            ends.append((to_mm(p.x), to_mm(p.y)))
    doomed = []
    for t in board.Tracks():
        if t.Type() != pcbnew.PCB_VIA_T:
            continue
        net = t.GetNet().GetNetname()
        if net not in PLANE_NETS or net == "GND":
            continue
        p = t.GetPosition()
        x, y = to_mm(p.x), to_mm(p.y)
        if not any((ex - x) ** 2 + (ey - y) ** 2 < 0.01 for ex, ey in ends):
            doomed.append(t)
    for t in doomed:
        board.Remove(t)
    return len(doomed)


def main() -> int:
    board = pcbnew.LoadBoard(str(BOARD))
    copper = Copper(board)
    added, failed = connect_plane_stubs(board, copper)
    hidden, moved = tidy_silkscreen(board)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(BOARD), board)
    print(f"plane stubs: {added} connected with a stub and via")
    if failed:
        print("  still unconnected: " + "; ".join(failed))
    dropped = drop_unused_anchors(board)
    print(f"silkscreen: {hidden} passive references hidden, {moved} moved clear")
    print(f"anchors: {dropped} unused plane vias removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
