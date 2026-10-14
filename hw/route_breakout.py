"""Pour the slot breakout's planes and stitch the supply pads to them.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/route_breakout.py

Reads ``build/x68000-breakout.kicad_pcb`` from ``layout_breakout.py``, adds
copper, writes it back. What it does:

* **GND on In1.Cu**, and **+5 V and VREF sharing In2.Cu**, split left/right
  along the translator column — an 8T245 has its A-side supply on the finger
  side and its B-side supply on the header side, so the split falls naturally
  between them.
* a stub and a via from every SMD pad on one of those nets down to its plane.

That removes the three largest nets from the routing problem entirely.

**Signal routing is not done here, and the reason is measured rather than
assumed.** Drawing a direct segment between each pair of pads gave 305 DRC
errors with every part on the top side, and 266 after moving the address
translators underneath. The geometry looks like three monotonic fans, which
would be routable that way, but a segment drawn between two pads knows nothing
about the pads and tracks it crosses on the way, and 48 of them cross a column
of ICs. Doing it properly needs obstacle avoidance and rip-up — an autorouter.
Export a Specctra DSN from pcbnew (File > Export; ``kicad-cli`` has no DSN
exporter) and run freerouting, or route it by hand from this starting point.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout_breakout import RIGHT  # noqa: E402  single source of the board depth

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "build" / "x68000-breakout.kicad_pcb"
EDGE = RIGHT - 1.0            # keep copper off the board edge

TRACK_W = 0.25          # mm; plenty for a 5 mA MIDI loop and a 68000 bus
PLANE_CLEAR = 0.3
VIA_D, VIA_DRILL = 0.6, 0.3

#: (net, layer, rectangle) — several rectangles per net is fine, KiCad merges
#: same-net zones that touch.
#:
#: Two things here were wrong the first time and both only showed up as
#: unconnected plane items after routing:
#:
#: * the split between +5 V and VREF has to fall **through** the translator
#:   column, not beside it. An 8T245 is 6 mm wide: its V_CCA pads land at
#:   x 67.1 and its V_CCB pads at x 72.9, so a split at x 78 put both of them
#:   over the +5 V pour and left every VREF pad 6 mm from its own plane.
#: * GND has to reach the edge fingers at x 46-54, which means a second
#:   rectangle inside the narrow tab, kept clear of the tab's own edges.
#: One polygon per pour, not two abutting rectangles: same-net zones that share
#: an edge are reported as intersecting rather than merged.
PLANES = [
    ("GND", pcbnew.In1_Cu, [(46, 48), (58, 48), (58, 41), (EDGE, 41),
                            (EDGE, 187), (58, 187), (58, 174), (46, 174)]),
    ("+5V", pcbnew.In2_Cu, [(46, 48), (58, 48), (58, 41), (70, 41),
                            (70, 187), (58, 187), (58, 174), (46, 174)]),
    ("VREF", pcbnew.In2_Cu, [(70.5, 41), (EDGE, 41), (EDGE, 187), (70.5, 187)]),
]
PLANE_NETS = {net for net, _, _ in PLANES}


def mm(v):
    return pcbnew.FromMM(v)


def to_mm(v):
    return pcbnew.ToMM(v)


def pt(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


#: SWIG hands the zone a raw pointer to the polygon, so if Python collects it
#: the board is left holding a dangling reference and the next save segfaults.
#: Keeping them alive here is the whole fix; it cost an afternoon to find.
_KEEPALIVE = []


def add_zone(board, net_name, layer, points):
    zone = pcbnew.ZONE(board)
    zone.SetLayer(layer)
    zone.SetNetCode(board.FindNet(net_name).GetNetCode())
    zone.SetLocalClearance(mm(PLANE_CLEAR))
    zone.SetMinThickness(mm(0.2))
    outline = pcbnew.SHAPE_POLY_SET()
    outline.NewOutline()
    for x, y in points:
        outline.Append(mm(x), mm(y))
    zone.SetOutline(outline)
    _KEEPALIVE.append(outline)
    # Full connection rather than thermal relief: on a plane that exists to
    # carry a 68000 bus's return current, spokes are a liability, and KiCad
    # reports starved thermals on the pads where only two fit.
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    zone.SetIsFilled(False)
    board.Add(zone)
    return zone


def add_track(board, net, layer, a, b, width=TRACK_W):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pt(*a))
    t.SetEnd(pt(*b))
    t.SetWidth(mm(width))
    t.SetLayer(layer)
    t.SetNet(net)
    board.Add(t)
    return t


def add_via(board, net, x, y, diameter=None, drill=None):
    """`diameter`/`drill` default to this module's constants, which is what the
    breakout uses; the logic board passes the smaller 0.45/0.25 explicitly so
    its vias match its netclass."""
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pt(x, y))
    v.SetWidth(mm(VIA_D if diameter is None else diameter))
    v.SetDrill(mm(VIA_DRILL if drill is None else drill))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNet(net)
    board.Add(v)
    return v


def pad_xy(pad):
    p = pad.GetPosition()
    return to_mm(p.x), to_mm(p.y)


def seg_distance(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5


def obstacles(board):
    """Pads and vias with their nets. Called before any routing exists, which
    is the whole point: a plane via placed now has room, and freerouting will
    route around it. Placed afterwards it has to squeeze into what is left,
    which is why the first version of this gave up on thirty pads."""
    out = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            p = pad.GetPosition()
            r = max(to_mm(pad.GetSize().x), to_mm(pad.GetSize().y)) / 2
            out.append((to_mm(p.x), to_mm(p.y), r, pad.GetNet().GetNetname()))
    for t in board.Tracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            p = t.GetPosition()
            out.append((to_mm(p.x), to_mm(p.y), VIA_D / 2,
                        t.GetNet().GetNetname()))
    return out


def via_clear(items, x, y, net, diameter=None) -> bool:
    """`diameter` defaults to this module's VIA_D. The logic board passes its
    own 0.45 mm: asking for 0.6 mm of clearance around a 0.45 mm via rejected
    most of its inward fanout ring, which is the same mistake as add_via()
    reading the global instead of the netclass."""
    r = (VIA_D if diameter is None else diameter) / 2 + 0.3
    for px, py, size, pnet in items:
        limit = r + size if pnet != net else VIA_DRILL + 0.5
        if (px - x) ** 2 + (py - y) ** 2 < limit ** 2:
            return False
    return True


def stub_clear(items, a, b, net) -> bool:
    r = TRACK_W / 2 + 0.3
    for px, py, size, pnet in items:
        if pnet != net and seg_distance((px, py), a, b) < r + size:
            return False
    return True


#: Where each net's anchors may go: the region its pour covers.
ANCHOR_REGIONS = {
    "GND": (59.0, 43.0, EDGE - 1.0, 186.0),
    "+5V": (59.0, 43.0, 69.0, 186.0),
    "VREF": (71.5, 43.0, EDGE - 1.0, 186.0),
}
ANCHOR_PITCH = 9.0


def stitch_plane_pads(board) -> int:
    """Scatter via anchors across each pour and let the router find them.

    Three attempts at giving every plane pad its own stub and via all made
    things worse -- 30 pads skipped, then 2 DRC violations, then 12 unconnected
    instead of 8 -- because a via hand-placed next to a 0.65 mm pitch pin is
    either in the way of the routing or unreachable once the routing is there.

    Freerouting is better at this than any heuristic here: it already treats
    GND, +5 V and VREF as nets to route. So put down a coarse grid of vias in
    clear space, one set per pour, and let it connect each pad to whichever
    anchor it can reach. The pours still carry the current; the anchors just
    give the router somewhere to aim.
    """
    made = 0
    items = obstacles(board)

    # A translator's supply pins are the hardest pads on the board to reach:
    # 0.65 mm pitch, routing on three sides. Give each one its own anchor just
    # off the end of the package, before anything else is in the way.
    for fp in board.GetFootprints():
        pads = {p.GetNumber(): p for p in fp.Pads()}
        if "23" not in pads or "1" not in pads:
            continue
        if pads["1"].GetNet().GetNetname() != "+5V":
            continue
        for number, dx in (("1", -5.5), ("23", 5.5)):
            pad = pads[number]
            net = pad.GetNet()
            x, y = pad_xy(pad)
            for offset in (dx, dx * 1.3, dx * 1.7):
                cand = (x + offset, y)
                if via_clear(items, *cand, net.GetNetname()):
                    add_via(board, net, *cand)
                    items.append((cand[0], cand[1], VIA_D / 2, net.GetNetname()))
                    made += 1
                    break

    for net_name, (x0, y0, x1, y1) in ANCHOR_REGIONS.items():
        net = board.FindNet(net_name)
        y = y0
        while y <= y1:
            x = x0
            while x <= x1:
                if via_clear(items, x, y, net_name):
                    add_via(board, net, x, y)
                    items.append((x, y, VIA_D / 2, net_name))
                    made += 1
                x += ANCHOR_PITCH
            y += ANCHOR_PITCH
    return made


skipped: list[str] = []


def main() -> int:
    board = pcbnew.LoadBoard(str(BOARD))

    for name, layer, points in PLANES:
        add_zone(board, name, layer, points)
    stitched = stitch_plane_pads(board)

    # Signal routing is deliberately NOT done here. Direct pad-to-pad segments
    # were tried and measured: 305 DRC errors with everything on one side, 266
    # with the address bundle moved underneath. A segment drawn between two
    # pads does not know about the pads and tracks in between, and this board
    # has 48 of them crossing a column of ICs. Getting it right needs obstacle
    # avoidance and rip-up, i.e. a real autorouter -- use freerouting, via
    # pcbnew's File > Export > Specctra DSN (kicad-cli cannot export DSN).
    routed = []

    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())

    pcbnew.SaveBoard(str(BOARD), board)
    print(f"zones: {len(PLANES)}  plane vias: {stitched}  "
          f"bundle tracks: {len(routed)}")
    if skipped:
        print(f"no room for a stitch via at: {', '.join(skipped)}")
    print(f"wrote {BOARD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
