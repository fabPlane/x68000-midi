#!/bin/sh
# Place, pour, route and check the slot breakout, end to end.
#
#   hw/build_breakout.sh
#
# Freerouting does the signal routing: KiCad's own autorouter was removed years
# ago, and the direct-segment router tried here first was measured at 266 DRC
# errors (see docs/DECISIONS.md). pcbnew's Python API can export the Specctra
# DSN and import the session back, so none of this needs the GUI.
set -e
cd "$(dirname "$0")/.."
KPY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
CLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
JAR=${FREEROUTING_JAR:-/Users/hyper/projects/tensorfleet/freerouting-otel/build/libs/freerouting-current-executable.jar}
PASSES=${PASSES:-20}

# The two boards only mate if their ribbons agree pin for pin, and
# neither board's own checks can see the other's.
"$KPY" hw/make_idc_footprint.py
python3 hw/check_mating.py
"$KPY" hw/layout_breakout.py
"$KPY" hw/route_breakout.py
"$KPY" - <<'PY'
import pcbnew
b = pcbnew.LoadBoard("build/x68000-breakout.kicad_pcb")
assert pcbnew.ExportSpecctraDSN(b, "build/x68000-breakout.dsn")
print("exported DSN", flush=True)
PY
# KiCad exports every copper layer as a signal layer, so freerouting happily
# routes across In1 and In2 and cuts the planes into islands -- which showed up
# as 34 unconnected plane fragments and not one unconnected signal. Declaring
# them as power layers keeps the pours whole and the return paths continuous.
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("build/x68000-breakout.dsn")
text = p.read_text()
for layer in ("In1.Cu", "In2.Cu"):
    text = re.sub(r"(\(layer %s\s*\(type )signal(\))" % re.escape(layer),
                  r"\1power\2", text)
p.write_text(text)
print("marked In1.Cu and In2.Cu as power layers", flush=True)
PY
# `< /dev/null` matters: run from a background shell without it, freerouting
# reads stdin, gets EOF and exits before routing anything -- and because its
# output went through a `|| true` pipe, the run looked like a success and left
# an unrouted board behind. The guard below is the second line of defence.
rm -f build/x68000-breakout.ses
# -Xmx: without a bound the JVM grew to 9.3 GB on the logic board and
# spent its time in GC -- 11% CPU after 21 minutes, no progress. The
# runs that finish peak around 3.8 GB.
java -Xmx6g -Djava.awt.headless=true -jar "$JAR" \
    -de build/x68000-breakout.dsn -do build/x68000-breakout.ses \
    -mp "$PASSES" -mt 4 < /dev/null 2>&1 \
    | grep -E "Auto-routing stage completed|Optimization stage completed" || true
test -s build/x68000-breakout.ses || {
    echo "freerouting produced no session file — stopping" >&2; exit 1; }
"$KPY" - <<'PY'
import pcbnew
b = pcbnew.LoadBoard("build/x68000-breakout.kicad_pcb")
assert pcbnew.ImportSpecctraSES(b, "build/x68000-breakout.ses")
tracks = len([t for t in b.Tracks() if t.Type() != pcbnew.PCB_VIA_T])
assert tracks > 500, f"only {tracks} tracks after import — the session is empty"
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
pcbnew.SaveBoard("build/x68000-breakout.kicad_pcb", b)
print(f"imported session, {tracks} tracks, zones filled", flush=True)
PY
# Connect the plane stubs the router leaves behind, then check for real.
"$CLI" pcb drc --severity-all --format json -o build/drc-all.json \
    build/x68000-breakout.kicad_pcb >/dev/null
"$KPY" hw/fix_planes.py
"$CLI" pcb drc --severity-all --format json -o build/drc-all.json \
    build/x68000-breakout.kicad_pcb >/dev/null
"$KPY" hw/tidy_board.py
"$KPY" hw/fix_last_gnd.py
"$CLI" pcb drc --severity-all --format json -o build/drc-all.json \
    build/x68000-breakout.kicad_pcb | grep Found
"$CLI" pcb export svg --layers F.Cu,B.Cu,In1.Cu,In2.Cu,F.SilkS,Edge.Cuts \
    --page-size-mode 2 --exclude-drawing-sheet \
    -o build/breakout-routed.svg build/x68000-breakout.kicad_pcb >/dev/null
echo "wrote build/x68000-breakout.kicad_pcb and build/breakout-routed.svg"
