"""Pour the logic board's planes and scatter via anchors for the router.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/route_logic.py

Same shape as `route_breakout.py`, and it borrows that module's helpers rather
than copying them; only the pour geometry differs, because this board has three
supply voltages instead of two references and no edge-connector tab to work
around.

Six layers. In1 is all ground and In4 is the power split -- 3.3 V under the
ribbons and the FPGA's 3.3 V banks, 1.2 V as a small island under the core,
1.8 V down the MIDI side where bank 3 and the PSRAM supply sit. In2 and In3 are
left alone for the router, because with only F.Cu and B.Cu to work with it left
85 of 165 nets unrouted.
"""

from __future__ import annotations

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

#: The FPGA sits at (40, 75) and is 10 mm square; its core rail only has to
#: reach it, so 1.2 V is an island rather than a stripe.
#: In1 is ground, In4 is the power split; In2 and In3 are left for the router.
PLANES = [
    ("GND", pcbnew.In1_Cu, [(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1)]),
    ("+3V3", pcbnew.In4_Cu, [(X0, Y0), (33.0, Y0), (33.0, Y1), (X0, Y1)]),
    ("+1V2", pcbnew.In4_Cu, [(33.5, 62.0), (47.0, 62.0), (47.0, 88.0),
                             (33.5, 88.0)]),
    ("+1V8", pcbnew.In4_Cu, [(47.5, Y0), (X1, Y0), (X1, Y1), (47.5, Y1)]),
]
PLANE_NETS = {net for net, _, _ in PLANES}

ANCHOR_REGIONS = {
    "GND": (X0 + 1, Y0 + 1, X1 - 1, Y1 - 1),
    "+3V3": (X0 + 1, Y0 + 1, 32.0, Y1 - 1),
    "+1V8": (48.5, Y0 + 1, X1 - 1, Y1 - 1),
    "+1V2": (34.5, 63.0, 46.0, 87.0),
}
ANCHOR_PITCH = 14.0
#: No anchors inside this radius of the FPGA; the escapes need the room.
KEEPOUT = 12.0   # 8 mm put 215 vias on a 70 x 150 board and freerouting never finished


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
            add_via(board, gnd, x, y)
            items.append((x, y, VIA_D / 2, "GND"))
            made += 1

    for net_name, (x0, y0, x1, y1) in ANCHOR_REGIONS.items():
        net = board.FindNet(net_name)
        y = y0
        while y <= y1:
            x = x0
            while x <= x1:
                near_fpga = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 < KEEPOUT
                if not near_fpga and via_clear(items, x, y, net_name):
                    add_via(board, net, x, y)
                    items.append((x, y, VIA_D / 2, net_name))
                    made += 1
                x += ANCHOR_PITCH
            y += ANCHOR_PITCH

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(BOARD), board)
    print(f"zones: {len(PLANES)}  plane vias: {made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
