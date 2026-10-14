"""Reach an isolated plane island out to somewhere a via will fit.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/stub_to_plane.py logic

The last plane gaps on these boards are not gaps between two things: they are
*islands*, pieces of +3.3 V or ground copper with no via down to their plane.
`fix_planes` looks for a via position on the island's existing tracks and
`tidy_board` will add a short stub to reach one, and on these three both give
up -- `fix_planes` because it asks for 0.2 mm of clearance around the via and
steps along tracks in 0.25 mm hops, `tidy_board` because it asks for 0.35 mm.

Both are more conservative than the board's own rule, which is 0.15 mm. At
0.15 mm, sampled at 0.02 mm, there is still no via position *on* these islands
-- that part was measured, not assumed. So the only way out is to add copper:
run a stub from a free end of the island into clear space and put the via
there.

The search is a fan: from each endpoint of the island, 24 directions, lengths
from 0.3 to 3.0 mm, on both outer layers, stub and via both tested against
every piece of copper on another net and every drilled hole. Shortest stub
wins. Nothing is placed unless DRC afterwards shows no new errors and no new
unconnected items.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
BOARDS = {"logic": ("x68000-logic.kicad_pcb", "drc-logic.json"),
          "breakout": ("x68000-breakout.kicad_pcb", "drc-all.json")}

VIA_D, VIA_DRILL = 0.45, 0.25
TRACK_W = 0.15
CLEAR = 0.15          # the board's own rule, not a padded version of it
HOLE = 0.25           # board minimum hole-to-hole
TOL = 0.01
#: Print each island's size and whether it already has a via. Worth having
#: on: it is what showed the search was 'fixing' the connected side of a
#: pair, and that an island was one orphan pad rather than a chain.
VERBOSE = True

LENGTHS = [0.3, 0.5, 0.7, 0.9, 1.2, 1.5, 2.0, 2.5, 3.0]
ANGLES = [i * math.pi / 12 for i in range(24)]


def mm(v):
    return pcbnew.FromMM(v)


def to_mm(v):
    return pcbnew.ToMM(v)


def seg_dist(p, a, e) -> float:
    (px, py), (ax, ay), (ex, ey) = p, a, e
    dx, dy = ex - ax, ey - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def seg_seg_dist(a1, a2, b1, b2) -> float:
    return min(seg_dist(a1, b1, b2), seg_dist(a2, b1, b2),
               seg_dist(b1, a1, a2), seg_dist(b2, a1, a2))


class Copper:
    """Everything that is not on `net`, by layer, plus every drilled hole."""

    def __init__(self, board, net):
        self.rects = {pcbnew.F_Cu: [], pcbnew.B_Cu: []}
        self.segs = {pcbnew.F_Cu: [], pcbnew.B_Cu: []}
        self.holes = []
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                d = to_mm(max(pad.GetDrillSize().x, pad.GetDrillSize().y))
                if d > 0:
                    self.holes.append((to_mm(pad.GetPosition().x),
                                       to_mm(pad.GetPosition().y), d / 2))
                if pad.GetNetname() == net:
                    continue
                b = pad.GetBoundingBox()
                r = (to_mm(b.GetLeft()), to_mm(b.GetTop()),
                     to_mm(b.GetRight()), to_mm(b.GetBottom()))
                for layer in self.rects:
                    if pad.IsOnLayer(layer):
                        self.rects[layer].append(r)
        for t in board.Tracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                p = t.GetPosition()
                self.holes.append((to_mm(p.x), to_mm(p.y),
                                   to_mm(t.GetDrillValue()) / 2))
                if t.GetNetname() == net:
                    continue
                x, y, r = to_mm(p.x), to_mm(p.y), VIA_D / 2
                for layer in self.rects:
                    self.rects[layer].append((x - r, y - r, x + r, y + r))
            elif t.GetNetname() != net:
                a, e = t.GetStart(), t.GetEnd()
                s = ((to_mm(a.x), to_mm(a.y)), (to_mm(e.x), to_mm(e.y)),
                     to_mm(t.GetWidth()) / 2)
                for layer in self.segs:
                    if t.IsOnLayer(layer):
                        self.segs[layer].append(s)

    def track_ok(self, a, b, layer) -> bool:
        r = TRACK_W / 2 + CLEAR
        for s1, s2, half in self.segs[layer]:
            if seg_seg_dist(a, b, s1, s2) < r + half:
                return False
        n = max(2, int(math.dist(a, b) / 0.05))
        for x0, y0, x1, y1 in self.rects[layer]:
            for i in range(n + 1):
                t = i / n
                x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
                dx = max(x0 - x, 0.0, x - x1)
                dy = max(y0 - y, 0.0, y - y1)
                if math.hypot(dx, dy) < r:
                    return False
        return True

    def via_ok(self, p) -> bool:
        r = VIA_D / 2 + CLEAR
        for layer in self.rects:
            for s1, s2, half in self.segs[layer]:
                if seg_dist(p, s1, s2) < r + half:
                    return False
            for x0, y0, x1, y1 in self.rects[layer]:
                dx = max(x0 - p[0], 0.0, p[0] - x1)
                dy = max(y0 - p[1], 0.0, p[1] - y1)
                if math.hypot(dx, dy) < r:
                    return False
        for hx, hy, hr in self.holes:
            if math.hypot(hx - p[0], hy - p[1]) < VIA_DRILL / 2 + hr + HOLE:
                return False
        return True


def island(board, net, seeds):
    """Track endpoints reachable from `seeds` through same-net tracks.

    Seed from ONE reported item, never both. The two items in an unconnected
    pair are on different groups by definition, so seeding from both merges
    them and the search then happily "fixes" the group that was already fine:
    the first run stubbed out from the via at (36.00, 78.80), which was the
    connected side, and the unconnected count did not move.
    """
    same = lambda p, q: abs(p[0]-q[0]) < TOL and abs(p[1]-q[1]) < TOL
    mine = []
    for t in board.Tracks():
        if t.Type() == pcbnew.PCB_VIA_T or t.GetNetname() != net:
            continue
        a, e = t.GetStart(), t.GetEnd()
        mine.append(((to_mm(a.x), to_mm(a.y)), (to_mm(e.x), to_mm(e.y)),
                     t.GetLayer()))
    # Same-net pads, as rectangles: a group also continues *through* a pad.
    # Walking shared track endpoints alone stopped this search at C10's pad 2
    # and hid everything the island reaches beyond it -- which was most of it,
    # and the part that is not buried under the data bus.
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() != net:
                continue
            b = pad.GetBoundingBox()
            pads.append((to_mm(b.GetLeft()), to_mm(b.GetTop()),
                         to_mm(b.GetRight()), to_mm(b.GetBottom())))
    inside = lambda p, r: r[0] - TOL <= p[0] <= r[2] + TOL and \
                          r[1] - TOL <= p[1] <= r[3] + TOL

    pts, layers, frontier, seen = [], set(), list(seeds), set()
    hit_pads = set()
    while frontier:
        pt = frontier.pop()
        for i, (a, e, layer) in enumerate(mine):
            if i in seen:
                continue
            touches = same(a, pt) or same(e, pt)
            if not touches:
                # ...or either end sits on a pad this group has already reached
                for pi in hit_pads:
                    if inside(a, pads[pi]) or inside(e, pads[pi]):
                        touches = True
                        break
            if touches:
                seen.add(i)
                layers.add(layer)
                pts += [a, e]
                frontier += [a, e]
        for pi, r in enumerate(pads):
            if pi not in hit_pads and inside(pt, r):
                hit_pads.add(pi)
                # re-walk: tracks on this pad are now in the group
                frontier += [pt]
                for a, e, layer in mine:
                    if inside(a, r) or inside(e, r):
                        frontier += [a, e]
    return pts or list(seeds), (layers or {pcbnew.F_Cu})


def has_via(board, net, pts) -> bool:
    """Is any same-net via sitting on one of this group's track endpoints?"""
    for t in board.Tracks():
        if t.Type() != pcbnew.PCB_VIA_T or t.GetNetname() != net:
            continue
        p = (to_mm(t.GetPosition().x), to_mm(t.GetPosition().y))
        if any(abs(p[0]-q[0]) < TOL and abs(p[1]-q[1]) < TOL for q in pts):
            return True
    return False


def main(argv) -> int:
    name = argv[1] if len(argv) > 1 else "logic"
    board_file, drc_file = BOARDS[name]
    path = ROOT / "build" / board_file
    report = ROOT / "build" / drc_file

    def measure():
        subprocess.run([CLI, "pcb", "drc", "--severity-all", "--format", "json",
                        "-o", str(ROOT / "build" / "drc-stub.json"), str(path)],
                       capture_output=True)
        d = json.loads((ROOT / "build" / "drc-stub.json").read_text())
        return (sum(1 for v in d["violations"] if v["severity"] == "error"),
                len(d["unconnected_items"]))

    entries = json.loads(report.read_text()).get("unconnected_items", [])
    before = measure()
    backup = path.with_suffix(".kicad_pcb.stubbak")
    shutil.copy(path, backup)

    board = pcbnew.LoadBoard(str(path))
    placed, failed = [], []
    for entry in entries:
        items = entry.get("items", [])
        net = next((i["description"].split("[")[1].split("]")[0]
                    for i in items if "[" in i.get("description", "")), None)
        if net is None or board.FindNet(net) is None:
            continue
        # Each side separately, and only the side that has no via of its own:
        # a group that already reaches its plane needs nothing.
        seeds = [(i["pos"]["x"], i["pos"]["y"]) for i in items]
        copper = Copper(board, net)
        pts, layers = [], set()
        for seed in seeds:
            group, group_layers = island(board, net, [seed])
            hv = has_via(board, net, group)
            if VERBOSE:
                print(f"   seed ({seed[0]:.3f},{seed[1]:.3f}): "
                      f"{len(set(group))} point(s), has_via={hv}")
            if hv:
                continue
            pts += group
            layers |= group_layers
        if not pts:
            failed.append(f"{net} near ({seeds[0][0]:.1f},{seeds[0][1]:.1f}) "
                          f"- both sides already have a via")
            continue
        best = None
        for start in pts:
            for length in LENGTHS:
                if best and length >= best[0]:
                    break
                for ang in ANGLES:
                    end = (start[0] + length * math.cos(ang),
                           start[1] + length * math.sin(ang))
                    for layer in sorted(layers):
                        if not copper.track_ok(start, end, layer):
                            continue
                        if not copper.via_ok(end):
                            continue
                        best = (length, start, end, layer)
                        break
                    if best:
                        break
        if best is None:
            failed.append(f"{net} near ({seeds[0][0]:.1f},{seeds[0][1]:.1f})")
            continue
        length, start, end, layer = best
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pcbnew.VECTOR2I(mm(start[0]), mm(start[1])))
        t.SetEnd(pcbnew.VECTOR2I(mm(end[0]), mm(end[1])))
        t.SetWidth(mm(TRACK_W))
        t.SetLayer(layer)
        t.SetNet(board.FindNet(net))
        board.Add(t)
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(mm(end[0]), mm(end[1])))
        v.SetWidth(mm(VIA_D))
        v.SetDrill(mm(VIA_DRILL))
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetNet(board.FindNet(net))
        board.Add(v)
        placed.append(f"{net}: {length:.2f} mm stub on "
                      f"{board.GetLayerName(layer)} from "
                      f"({start[0]:.2f},{start[1]:.2f}) to a via at "
                      f"({end[0]:.2f},{end[1]:.2f})")

    if not placed:
        backup.unlink(missing_ok=True)
        print("nothing placed")
        for f in failed:
            print(f"  no stub-and-via for {f}")
        return 0

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(path), board)
    after = measure()
    if after[0] > before[0] or after[1] > before[1]:
        shutil.copy(backup, path)
        backup.unlink()
        print(f"placed {len(placed)} but (errors, unconnected) went "
              f"{before} -> {after}; reverted")
        return 1
    backup.unlink()
    print(f"(errors, unconnected) {before} -> {after}")
    for p in placed:
        print(f"  {p}")
    for f in failed:
        print(f"  no stub-and-via for {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
