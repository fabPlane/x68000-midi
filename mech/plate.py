"""Slot cover plate for the X68000 MIDI card.

    /Users/hyper/projects/fabPlane/midi_box/.venv/bin/python mech/plate.py

Writes `build/plate.stl` and `build/plate.dxf` (the DXF is the laser/mill
profile; the STL is for printing one at home).

The outline is midiori's `midiori_2_plate_minidin_laser.svg` -- a known-good fit
in a real slot, which is the whole reason to start from it rather than measure a
machine we do not have:

    0,20 -> 0,5 -> 30,5 -> 30,0 -> 143,0 -> 143,5 -> 176,5 -> 176,20 -> close

i.e. 176 x 20 mm with a 113 mm long, 5 mm deep step along the bottom edge for
the slot lip. Ours differs only in what is cut through it: three 3.5 mm TRS
jacks instead of one mini-DIN, plus the USB-C opening for the JTAG bridge.

Hole sizes and positions are *measured out of* `midiori_2_plate_trs.stl` rather
than guessed: its jack bore is 7.0 mm at y = 12.5, and its two M3 clearance
holes are 4.0 mm at x = 3.51 and 169.51, y = 8.5 (166 mm apart, not the 136 mm
of the PCB's own mounting holes). The middle of our three jacks sits exactly
where midiori's single one does, so that one at least is known to clear.

Jack spacing is 22 mm, which clears the bezel of a panel-nut 3.5 mm jack with
room for a plug's overmould either side and keeps every opening inside the
plate's straight section.
"""

from pathlib import Path

import cadquery as cq

THICK = 2.0
LEN, HEIGHT = 176.0, 20.0
STEP_X0, STEP_X1, STEP_DEPTH = 30.0, 143.0, 5.0

#: Row height for the jacks and the USB opening, from midiori's plate.
OPENING_Y = 12.5

#: 3.5 mm panel-nut jack bore, as midiori cuts it.
JACK_D = 7.0
JACK_PITCH = 22.0
JACK_MID_X = 78.01                  # midiori's jack; ours is the middle of three
JACK_LABELS = ("OUT", "OUT 2", "IN")

#: USB-C receptacle cutout, 9.0 x 3.2 mm plus 0.3 mm clearance.
USB_W, USB_H = 9.3, 3.5
USB_X = 136.0

#: M3 clearance, measured from midiori's plate: 4.0 mm at y = 8.5, 166 apart.
M3 = 4.0
M3_Y = 8.5
M3_X = (3.51, 169.51)


def profile():
    return (
        cq.Workplane("XY")
        .moveTo(0, HEIGHT).lineTo(0, STEP_DEPTH)
        .lineTo(STEP_X0, STEP_DEPTH).lineTo(STEP_X0, 0)
        .lineTo(STEP_X1, 0).lineTo(STEP_X1, STEP_DEPTH)
        .lineTo(LEN, STEP_DEPTH).lineTo(LEN, HEIGHT)
        .close()
    )


def plate():
    body = profile().extrude(THICK)
    body = (body.faces(">Z").workplane(origin=(0, 0, 0))
            .pushPoints([(x, OPENING_Y) for x in jack_x()]).hole(JACK_D))
    body = (body.faces(">Z").workplane(origin=(0, 0, 0))
            .pushPoints([(USB_X, OPENING_Y)]).rect(USB_W, USB_H).cutThruAll())
    body = (body.faces(">Z").workplane(origin=(0, 0, 0))
            .pushPoints([(x, M3_Y) for x in M3_X]).hole(M3))
    return body


def jack_x():
    return [JACK_MID_X + (i - 1) * JACK_PITCH for i in range(3)]


def main():
    out = Path(__file__).resolve().parent.parent / "build"
    out.mkdir(exist_ok=True)
    body = plate()
    cq.exporters.export(body, str(out / "plate.stl"))
    cq.exporters.export(body.faces("<Z").wires().toPending(),
                        str(out / "plate.dxf"))
    bb = body.val().BoundingBox()
    print(f"plate {bb.xlen:.1f} x {bb.ylen:.1f} x {bb.zlen:.1f} mm")
    for label, x in zip(JACK_LABELS, jack_x()):
        print(f"  {label:<5} jack centre x={x:.2f} y={OPENING_Y}  bore {JACK_D}")
    print(f"  USB-C cutout x={USB_X:.1f} y={OPENING_Y}  {USB_W} x {USB_H}")
    print(f"  M3 clearance {M3} at x={M3_X[0]}, {M3_X[1]}  y={M3_Y}")
    print(f"  wrote {out/'plate.stl'} and {out/'plate.dxf'}")


if __name__ == "__main__":
    main()
