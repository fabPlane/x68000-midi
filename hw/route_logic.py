"""Pour the logic board's planes and scatter via anchors for the router.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/route_logic.py

Same shape as `route_breakout.py`, and it borrows that module's helpers rather
than copying them; only the pour geometry differs, because this board has three
supply voltages instead of two references and no edge-connector tab to work
around.

Four layers: F.Cu signal, In1 all ground, In2 all +3.3 V, B.Cu signal.

The earlier board split In4 three ways -- 3.3 V left of x = 33, a 1.2 V island
under the core, 1.8 V right of x = 47.5 -- and the split did not survive
contact with the package's pinout. The FPGA's three supplies are interleaved
around one perimeter row, so no stripe can reach them all: pin 12 (+1.8 V, the
PSRAM's bank) sits on the west face at (35.2, 75.2), 12 mm from where the
1.8 V stripe began, and six of the seven +3.3 V pins were over no 3.3 V copper
at all. Every one of those pins had to cross the board on a signal layer to
find its own rail.

So only the rail that is genuinely a plane gets one. +3.3 V has six FPGA pins
plus the ribbons, the MIDI drivers, the opto and the oscillator, and it takes
In2 solid. +1.8 V has one FPGA pin and +1.2 V has four, and both regulators now
sit next to those pins, so they are ordinary routed nets -- a 10 mm track from
an adjacent regulator beats a plane stripe that stops short of the pad.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout_logic import HEIGHT, WIDTH  # noqa: E402
from route_breakout import (VIA_D, add_track, add_via,  # noqa: E402
                            add_zone, obstacles, pad_xy, to_mm, via_clear)

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "build" / "x68000-logic.kicad_pcb"

INSET = 2.0
X0, Y0, X1, Y1 = INSET, INSET, WIDTH - INSET, HEIGHT - INSET

#: Two solid planes, one per inner layer. +1.8 V and +1.2 V are not here: they
#: are routed nets, fed by regulators placed at their pins.
PLANES = [
    ("GND", pcbnew.In1_Cu, [(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1)]),
    ("+3V3", pcbnew.In2_Cu, [(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1)]),
]
PLANE_NETS = {net for net, _, _ in PLANES}

#: Only the two plane nets get anchors; a net the router has to wire itself
#: must not have stray vias dropped on it.
ANCHOR_REGIONS = {
    "GND": (X0 + 1, Y0 + 1, X1 - 1, Y1 - 1),
    "+3V3": (X0 + 1, Y0 + 1, X1 - 1, Y1 - 1),
}
#: 20 mm, not 14. The grid's job is plane stitching -- return-current paths
#: between the two inner layers -- now that every plane *pad* has its own via
#: beside it. At 14 mm the grid added 74 vias on top of those 69 and the
#: router ran out of room: three ribbon signals (HD10, HA22, HIDDIR) went
#: unrouted where the same board with a coarser grid routed all sixty.
ANCHOR_PITCH = 20.0
#: Matching the netclass, not route_breakout's 0.6/0.3 -- add_via() reads its
#: own module globals, so these have to be passed in at every call.
VIA_DIA, VIA_DRL = 0.45, 0.25
#: No anchors inside this radius of the FPGA; the escapes need the room.
KEEPOUT = 12.0   # 8 mm put 215 vias on a 70 x 150 board and freerouting never finished


def pad_rects(board):
    """Every pad as an axis-aligned rectangle in mm, with its net.

    `route_breakout.via_clear` models a pad as a circle of radius max(w, h)/2,
    which is right for the breakout's square-ish pads and far too pessimistic
    for a 0.22 x 0.55 mm QFN land: it rejected every via in the fanout ring,
    including each one's own pad, over gaps that are really 0.27 mm.
    """
    out = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            b = pad.GetBoundingBox()
            out.append((to_mm(b.GetLeft()), to_mm(b.GetTop()),
                        to_mm(b.GetRight()), to_mm(b.GetBottom()),
                        pad.GetNetname()))
    return out


def rect_gap(x, y, rect) -> float:
    x0, y0, x1, y1, _ = rect
    dx = max(x0 - x, 0.0, x - x1)
    dy = max(y0 - y, 0.0, y - y1)
    return (dx * dx + dy * dy) ** 0.5


#: 0.19, not the 0.15 netclass minimum. A via placed with *exactly* the rule's
#: clearance leaves the router nothing: it then draws its own tracks to 0.15 as
#: well and the pair lands a few microns short, which is where the 0.1444 and
#: 0.1489 mm clearance errors kept coming from. The margin costs a few fanout
#: spots and removes a whole class of error.
VIA_MARGIN = 0.19


def ring_clear(rects, vias, x, y, net, clearance=VIA_MARGIN) -> bool:
    """Room for a fanout via at (x, y): clear of every pad on another net, and
    not on top of another via. A pad on the *same* net needs no gap -- that is
    the pad this via exists to connect."""
    r = VIA_DIA / 2
    for rect in rects:
        if rect[4] == net:
            continue
        if rect_gap(x, y, rect) < r + clearance:
            return False
    for vx, vy, vr, vnet in vias:
        limit = r + vr + clearance if vnet != net else VIA_DRL + 0.5
        if (vx - x) ** 2 + (vy - y) ** 2 < limit ** 2:
            return False
    return True


def main() -> int:
    board = pcbnew.LoadBoard(str(BOARD))
    for name, layer, points in PLANES:
        add_zone(board, name, layer, points)

    items = obstacles(board)
    made = 0

    # Nothing goes near the FPGA. The first version gave each of its eighteen
    # plane pads an anchor 7-10 mm from the centre -- a ring of vias 2-4 mm
    # outside a package whose pads end at 5 mm, which is precisely the annulus
    # all sixty signal escapes have to cross. Freerouting left 85 of 165 nets
    # unrouted on four layers and 92 on six; the layer count was never the
    # problem, the fence was.
    #
    # Ground gets thermal vias inside the exposed pad instead, which is what a
    # QFN wants anyway, and the supply pins reach the pours through the grid
    # below like everything else.
    fpga = next(f for f in board.GetFootprints() if f.GetReference() == "U1")
    cx, cy = to_mm(fpga.GetPosition().x), to_mm(fpga.GetPosition().y)
    gnd = board.FindNet("GND")
    for i in (-2, -1, 0, 1, 2):
        for j in (-2, -1, 0, 1, 2):
            x, y = cx + i * 1.3, cy + j * 1.3
            add_via(board, gnd, x, y, VIA_DIA, VIA_DRL)
            items.append((x, y, VIA_DIA / 2, "GND"))
            made += 1

    # The FPGA's own supply and ground pads cannot reach the generic anchor
    # grid: it keeps 12 mm clear of the package, and the escape fills what is
    # left. So they fan out *inward* instead, into the ring between the inner
    # edge of the pad row and the exposed pad -- 1.155 mm of it, enough for a
    # 0.45 mm via with 0.35 mm either side. That space is inside the package
    # and no signal escape crosses it, so a via there costs the router nothing.
    EP_HALF = 6.74 / 2          # exposed pad
    RING = 4.00                 # via centres, between 3.37 and 4.525
    rects = pad_rects(board)
    ring_vias = [(vx, vy, vr, vn) for vx, vy, vr, vn in items]
    inward, missed = 0, []
    for pad in fpga.Pads():
        name = pad.GetNet().GetNetname()
        if name not in PLANE_NETS or pad.GetNumber() in ("", "89"):
            continue
        px, py = to_mm(pad.GetPosition().x), to_mm(pad.GetPosition().y)
        dx, dy = px - cx, py - cy
        # Walk in along the edge normal: whichever axis the pad is furthest
        # out on is the edge it sits on. Straight in from the pad is the first
        # choice, but the ring is shared with the neighbouring pads' own
        # clearance, so slide along the face and in and out a little to find a
        # spot -- the alternative is giving up on half of them, and a supply
        # pin that reaches its plane 0.3 mm off-centre is no worse for it.
        vertical = abs(dy) >= abs(dx)
        sign = (1 if dy > 0 else -1) if vertical else (1 if dx > 0 else -1)
        spot = None
        for radius in (RING, RING + 0.1, RING - 0.1, RING + 0.15):
            for slide in (0.0, 0.3, -0.3, 0.6, -0.6, 0.9, -0.9):
                x = px + slide if vertical else cx + radius * sign
                y = cy + radius * sign if vertical else py + slide
                if max(abs(x - cx), abs(y - cy)) <= EP_HALF / 2:
                    continue
                if ring_clear(rects, ring_vias, x, y, name):
                    spot = (x, y)
                    break
            if spot:
                break
        if spot is None:
            missed.append(f"{pad.GetNumber()} {name}")
            continue
        x, y = spot
        add_via(board, board.FindNet(name), x, y, VIA_DIA, VIA_DRL)
        items.append((x, y, VIA_DIA / 2, name))
        ring_vias.append((x, y, VIA_DIA / 2, name))
        inward += 1
        made += 1

    # Every other pad on a plane net gets its own via, placed now, while there
    # is still room. The 14 mm anchor grid was never going to do this: a
    # decoupling capacitor 7 mm from the nearest grid point has to be routed
    # there, and after routing the via passes had nowhere left to put one --
    # which is why +3.3 V kept coming back as a dozen unconnected clusters.
    #
    # This is not the via fence that wrecked the earlier attempts. That was a
    # ring of vias in the annulus 5-12 mm from the FPGA, where all sixty signal
    # escapes have to cross; these sit beside pads elsewhere on the board, and
    # anything inside the keepout is still skipped.
    beside = 0
    for fp in board.GetFootprints():
        if fp.GetReference() == "U1":
            continue                      # handled by the inward ring above
        for pad in fp.Pads():
            name = pad.GetNetname()
            if name not in PLANE_NETS:
                continue
            px, py = to_mm(pad.GetPosition().x), to_mm(pad.GetPosition().y)
            spot = None
            for radius in (0.9, 1.1, 1.35, 1.6):
                for k in range(12):
                    ang = k * math.pi / 6
                    x, y = px + radius * math.cos(ang), py + radius * math.sin(ang)
                    if not (X0 <= x <= X1 and Y0 <= y <= Y1):
                        continue
                    if ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 < KEEPOUT:
                        continue
                    if ring_clear(rects, ring_vias, x, y, name):
                        spot = (x, y)
                        break
                if spot:
                    break
            if spot is None:
                continue
            x, y = spot
            add_via(board, board.FindNet(name), x, y, VIA_DIA, VIA_DRL)
            items.append((x, y, VIA_DIA / 2, name))
            ring_vias.append((x, y, VIA_DIA / 2, name))
            beside += 1
            made += 1

    for net_name, (x0, y0, x1, y1) in ANCHOR_REGIONS.items():
        net = board.FindNet(net_name)
        y = y0
        while y <= y1:
            x = x0
            while x <= x1:
                near_fpga = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 < KEEPOUT
                # ring_clear, not via_clear: a 1.7 mm square PTH pad modelled
                # as a circle of radius 0.85 hides its own corner by 0.35 mm,
                # and the stitching grid duly put a GND via 0.09 mm from the
                # ribbon's +3.3 V pin.
                if not near_fpga and ring_clear(rects, ring_vias, x, y, net_name):
                    add_via(board, net, x, y, VIA_DIA, VIA_DRL)
                    items.append((x, y, VIA_DIA / 2, net_name))
                    ring_vias.append((x, y, VIA_DIA / 2, net_name))
                    made += 1
                x += ANCHOR_PITCH
            y += ANCHOR_PITCH

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(BOARD), board)
    print(f"zones: {len(PLANES)}  plane vias: {made} "
          f"({inward} inward from the FPGA's supply pads, "
          f"{beside} beside other plane pads)")
    if missed:
        print(f"  no ring spot for: {', '.join(missed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
