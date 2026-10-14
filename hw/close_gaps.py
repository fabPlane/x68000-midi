"""Join the last few same-net gaps the router left, straight, and only if clear.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/close_gaps.py logic

`fix_planes` drops a via where one fits on existing copper and `tidy_board`
will add a short stub to reach one. Neither joins two pieces of copper that are
already on the same net and simply do not touch -- two track fragments a
millimetre apart, or a pad beside its own via. That is what is left on the
logic board after routing: three gaps, all on +3.3 V or ground.

A straight track between the two reported positions is the whole fix, and the
only question is whether it fits. So this tests the candidate against every
piece of copper on another net -- pads as rectangles, tracks as segments, vias
as circles -- and draws it only if it clears. Then DRC runs again: if the error
count went up, the change is reverted rather than argued with.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
BOARDS = {"logic": ("x68000-logic.kicad_pcb", "drc-logic.json"),
          "breakout": ("x68000-breakout.kicad_pcb", "drc-all.json")}
CLEAR = 0.15


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


def seg_seg_distance(a1, a2, b1, b2) -> float:
    return min(seg_distance(a1, b1, b2), seg_distance(a2, b1, b2),
               seg_distance(b1, a1, a2), seg_distance(b2, a1, a2))


def obstacles(board, layer, net):
    """Everything on `layer` that is not on `net`, as segments and rectangles."""
    segs, rects, vias = [], [], []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() == net or not pad.IsOnLayer(layer):
                continue
            b = pad.GetBoundingBox()
            rects.append((to_mm(b.GetLeft()), to_mm(b.GetTop()),
                          to_mm(b.GetRight()), to_mm(b.GetBottom())))
    for t in board.Tracks():
        if t.GetNetname() == net:
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            p = t.GetPosition()
            vias.append((to_mm(p.x), to_mm(p.y), 0.45 / 2))
        elif t.IsOnLayer(layer):
            a, b = t.GetStart(), t.GetEnd()
            segs.append(((to_mm(a.x), to_mm(a.y)), (to_mm(b.x), to_mm(b.y)),
                         to_mm(t.GetWidth()) / 2))
    return segs, rects, vias


def fits(a, b, width, segs, rects, vias) -> bool:
    r = width / 2 + CLEAR
    for s1, s2, half in segs:
        if seg_seg_distance(a, b, s1, s2) < r + half:
            return False
    for vx, vy, vr in vias:
        if seg_distance((vx, vy), a, b) < r + vr:
            return False
    for x0, y0, x1, y1 in rects:
        # Sample the candidate against the rectangle; the segment is short.
        n = max(2, int((((b[0]-a[0])**2 + (b[1]-a[1])**2) ** 0.5) / 0.05))
        for i in range(n + 1):
            t = i / n
            x, y = a[0] + (b[0]-a[0]) * t, a[1] + (b[1]-a[1]) * t
            dx = max(x0 - x, 0.0, x - x1)
            dy = max(y0 - y, 0.0, y - y1)
            if (dx * dx + dy * dy) ** 0.5 < r:
                return False
    return True


def candidates(a, b):
    """Paths from a to b, simplest first: straight, then the two L-shaped
    routes, then the four 45-degree dog-legs. Nothing fancier -- if none of
    seven paths fits, the gap wants a human looking at it, not a search."""
    ax, ay = a
    bx, by = b
    yield [a, b]
    yield [a, (bx, ay), b]
    yield [a, (ax, by), b]
    dx, dy = bx - ax, by - ay
    step = min(abs(dx), abs(dy))
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    # 45 then straight, and straight then 45, in both orientations.
    yield [a, (ax + sx * step, ay + sy * step), b]
    yield [a, (bx - sx * step, by - sy * step), b]


def score(path: Path, report: Path) -> tuple[int, int]:
    """(errors, unconnected). Both matter: a track that joins two fragments can
    isolate copper somewhere else, and guarding on the error count alone let
    the breakout go from five unconnected items to six while reporting
    success."""
    subprocess.run([CLI, "pcb", "drc", "--severity-all", "--format", "json",
                    "-o", str(report), str(path)], capture_output=True)
    d = json.loads(report.read_text())
    errs = sum(1 for v in d.get("violations", [])
               if v.get("severity") == "error")
    return errs, len(d.get("unconnected_items", []))


def main(argv) -> int:
    name = argv[1] if len(argv) > 1 else "logic"
    board_file, drc_file = BOARDS[name]
    path = ROOT / "build" / board_file
    report = ROOT / "build" / drc_file
    if not report.exists():
        raise SystemExit(f"{report} not found — build that board first")

    unconnected = json.loads(report.read_text()).get("unconnected_items", [])
    before = score(path, ROOT / "build" / "drc-gaps.json")
    backup = path.with_suffix(".kicad_pcb.gapbak")
    shutil.copy(path, backup)

    board = pcbnew.LoadBoard(str(path))
    width = board.GetDesignSettings().m_NetSettings \
                 .GetDefaultNetclass().GetTrackWidth()
    drawn, skipped = 0, []
    for entry in unconnected:
        items = entry.get("items", [])
        if len(items) != 2:
            continue
        net = None
        for it in items:
            d = it.get("description", "")
            if "[" in d:
                net = d.split("[")[1].split("]")[0]
                break
        if net is None or board.FindNet(net) is None:
            continue
        a = (items[0]["pos"]["x"], items[0]["pos"]["y"])
        b = (items[1]["pos"]["x"], items[1]["pos"]["y"])
        placed = False
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            segs, rects, vias = obstacles(board, layer, net)
            # NOT `path`: that is the board file, and shadowing it here meant
            # SaveBoard() wrote the board to a filename built out of a list of
            # coordinates while reporting success. Four such files turned up in
            # `git status`, which is the only reason it was noticed.
            for route in candidates(a, b):
                if not all(fits(p, q, to_mm(width), segs, rects, vias)
                           for p, q in zip(route, route[1:])):
                    continue
                for p, q in zip(route, route[1:]):
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(pcbnew.VECTOR2I(mm(p[0]), mm(p[1])))
                    t.SetEnd(pcbnew.VECTOR2I(mm(q[0]), mm(q[1])))
                    t.SetWidth(width)
                    t.SetLayer(layer)
                    t.SetNet(board.FindNet(net))
                    board.Add(t)
                drawn += 1
                placed = True
                break
            if placed:
                break
        if not placed:
            skipped.append(f"{net} ({a[0]:.1f},{a[1]:.1f})->({b[0]:.1f},{b[1]:.1f})")

    if not drawn:
        print(f"nothing to join ({len(skipped)} gap(s) had no clear path)")
        backup.unlink(missing_ok=True)
        return 0

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(path), board)
    after = score(path, ROOT / "build" / "drc-gaps.json")
    if after[0] > before[0] or after[1] > before[1]:
        shutil.copy(backup, path)
        backup.unlink()
        print(f"joined {drawn} gap(s) but (errors, unconnected) went "
              f"{before} -> {after}; reverted")
        return 1
    backup.unlink()
    print(f"joined {drawn} gap(s); (errors, unconnected) {before} -> {after}")
    if skipped:
        print("  no clear path for: " + "; ".join(skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
