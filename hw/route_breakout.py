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

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "build" / "x68000-breakout.kicad_pcb"

TRACK_W = 0.25          # mm; plenty for a 5 mA MIDI loop and a 68000 bus
PLANE_CLEAR = 0.3
VIA_D, VIA_DRILL = 0.6, 0.3

PLANES = {
    #  net    layer         rectangle (x0, y0, x1, y1) in board mm
    "GND": (pcbnew.In1_Cu, (56.0, 40.0, 119.0, 188.0)),
    "+5V": (pcbnew.In2_Cu, (56.0, 40.0, 78.0, 188.0)),
    "VREF": (pcbnew.In2_Cu, (78.5, 40.0, 119.0, 188.0)),
}


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


def add_zone(board, net_name, layer, rect):
    x0, y0, x1, y1 = rect
    zone = pcbnew.ZONE(board)
    zone.SetLayer(layer)
    zone.SetNetCode(board.FindNet(net_name).GetNetCode())
    zone.SetLocalClearance(mm(PLANE_CLEAR))
    zone.SetMinThickness(mm(0.2))
    outline = pcbnew.SHAPE_POLY_SET()
    outline.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
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


def add_via(board, net, x, y):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pt(x, y))
    v.SetWidth(mm(VIA_D))
    v.SetDrill(mm(VIA_DRILL))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNet(net)
    board.Add(v)
    return v


def pad_xy(pad):
    p = pad.GetPosition()
    return to_mm(p.x), to_mm(p.y)


def clear_of_obstacles(board, xy, radius) -> bool:
    """True if a via at `xy` keeps `radius` clear of every pad and via."""
    x, y = xy
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            px, py = pad_xy(pad)
            size = max(to_mm(pad.GetSize().x), to_mm(pad.GetSize().y)) / 2
            if (px - x) ** 2 + (py - y) ** 2 < (radius + size) ** 2:
                return False
    for track in board.Tracks():
        if track.Type() == pcbnew.PCB_VIA_T:
            vx, vy = to_mm(track.GetPosition().x), to_mm(track.GetPosition().y)
            if (vx - x) ** 2 + (vy - y) ** 2 < (radius + VIA_D / 2 + 0.2) ** 2:
                return False
    return True


skipped: list[str] = []


def stitch_plane_pads(board) -> int:
    """Every SMD pad on a plane net gets a short stub and a via down to it.

    Through-hole pads already pass through every layer, so they need nothing.
    The stub goes straight out from the pad, away from its footprint's centre,
    which for a two-row IC or an 0603 is the direction with room in it.
    """
    made = 0
    for fp in board.GetFootprints():
        cx, cy = to_mm(fp.GetPosition().x), to_mm(fp.GetPosition().y)
        for pad in fp.Pads():
            net = pad.GetNet()
            if net.GetNetname() not in PLANES:
                continue
            if pad.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            x, y = pad_xy(pad)
            dx, dy = x - cx, y - cy
            layer = pcbnew.F_Cu if pad.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
            # Step out from the pad, away from the footprint's middle, until
            # the via clears every hole and pad already on the board. A fixed
            # 0.9 mm stub lands on a neighbour often enough to matter.
            # Straight out along the dominant axis, at increasing reach.
            # Trying eight directions instead was measured and is worse: it
            # places ~20 more stitch vias but crowds the routing channels
            # enough to cost 2 DRC violations, and zero errors is worth more
            # than a handful of pre-made plane connections.
            spot = None
            for reach in (0.9, 1.3, 1.7, 2.2, 2.8):
                if abs(dx) >= abs(dy):
                    cand = (x + (reach if dx >= 0 else -reach), y)
                else:
                    cand = (x, y + (reach if dy >= 0 else -reach))
                if clear_of_obstacles(board, cand, VIA_D / 2 + 0.3):
                    spot = cand
                    break
            if spot is None:
                skipped.append(f"{fp.GetReference()}.{pad.GetNumber()}")
                continue
            add_track(board, net, layer, (x, y), spot)
            add_via(board, net, *spot)
            made += 1
    return made


def pads_of(board, ref):
    for fp in board.GetFootprints():
        if fp.GetReference() == ref:
            return {p.GetNumber(): p for p in fp.Pads()}
    raise SystemExit(f"no footprint {ref}")


def signal_bundles(board):
    """(net, from-pad, to-pad, layer) for every two-pad signal net, grouped so
    each group is monotonic in y."""
    out = []
    j1 = pads_of(board, "J1")
    for ref, a_first, nets_a in (
        ("U1", 3, [f"SD{i}" for i in range(8)]),
        ("U2", 3, [f"SD{i}" for i in range(8, 16)]),
        ("U3", 3, [f"SA{i}" for i in range(1, 9)]),
        ("U4", 3, [f"SA{i}" for i in range(9, 17)]),
        ("U5", 3, [f"SA{i}" for i in range(17, 24)] + ["CLK10M"]),
        ("U6", 3, ["AS_N", "UDS_N", "LDS_N", "RW", "IACK2_N", "IACK4_N",
                   "EXRESET_N", "IDDIR"]),
    ):
        pads = pads_of(board, ref)
        for i, name in enumerate(nets_a):
            net = board.FindNet(name)
            finger = next((p for p in j1.values() if p.GetNet().GetNetname() == name),
                          None)
            if finger is None:
                continue
            layer = pcbnew.F_Cu if finger.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
            out.append((name, finger, pads[str(a_first + i)], layer))
    return out


def main() -> int:
    board = pcbnew.LoadBoard(str(BOARD))

    for name, (layer, rect) in PLANES.items():
        add_zone(board, name, layer, rect)
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
