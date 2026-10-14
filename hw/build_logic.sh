#!/bin/sh
# Place, pour, route and check the logic board. Mirror of build_breakout.sh.
set -e
cd "$(dirname "$0")/.."
KPY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
CLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
JAR=${FREEROUTING_JAR:-/Users/hyper/projects/tensorfleet/freerouting-otel/build/libs/freerouting-current-executable.jar}
PASSES=${PASSES:-100}

"$KPY" hw/make_qfn_footprint.py
"$KPY" hw/make_idc_footprint.py
# The two boards only mate if their ribbons agree pin for pin, and
# neither board's own checks can see the other's.
python3 hw/check_mating.py
"$KPY" hw/layout_logic.py
"$KPY" hw/route_logic.py
"$KPY" - <<'PY'
import pcbnew
b = pcbnew.LoadBoard("build/x68000-logic.kicad_pcb")
assert pcbnew.ExportSpecctraDSN(b, "build/x68000-logic.dsn")
print("exported DSN", flush=True)
PY
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("build/x68000-logic.dsn")
t = p.read_text()
for layer in ("In1.Cu", "In2.Cu"):   # both inner layers are planes now
    t = re.sub(r"(\(layer %s\s*\(type )signal(\))" % re.escape(layer), r"\1power\2", t)
p.write_text(t)
print("marked In1.Cu and In2.Cu as power layers", flush=True)
PY
rm -f build/x68000-logic.ses
# -Xmx: without a bound the JVM grew to 9.3 GB on the logic board and
# spent its time in GC -- 11% CPU after 21 minutes, no progress. The
# runs that finish peak around 3.8 GB.
java -Xmx6g -Djava.awt.headless=true -jar "$JAR" \
    -de build/x68000-logic.dsn -do build/x68000-logic.ses \
    -mp "$PASSES" -mt 4 < /dev/null 2>&1 \
    | grep -E "Auto-routing stage completed|Optimization stage completed" || true
test -s build/x68000-logic.ses || {
    echo "freerouting produced no session file — stopping" >&2; exit 1; }
"$KPY" - <<'PY'
import pcbnew
b = pcbnew.LoadBoard("build/x68000-logic.kicad_pcb")
assert pcbnew.ImportSpecctraSES(b, "build/x68000-logic.ses")
tracks = len([t for t in b.Tracks() if t.Type() != pcbnew.PCB_VIA_T])
assert tracks > 300, f"only {tracks} tracks after import — the session is empty"
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
pcbnew.SaveBoard("build/x68000-logic.kicad_pcb", b)
print(f"imported session, {tracks} tracks, zones filled", flush=True)
PY
"$KPY" hw/widen_thin.py build/x68000-logic.kicad_pcb
# First DRC feeds fix_planes, which works from the unconnected list rather than
# guessing; the second is the one that counts.
"$CLI" pcb drc --severity-all --format json -o build/drc-logic.json \
    build/x68000-logic.kicad_pcb | grep Found
"$KPY" hw/fix_planes.py logic
"$CLI" pcb drc --severity-all --format json -o build/drc-logic.json \
    build/x68000-logic.kicad_pcb | grep Found
# tidy_board goes further than fix_planes: a short stub track as well as a via,
# which is what the crowded supply pads need. It also clears the silkscreen.
"$KPY" hw/tidy_board.py logic
"$CLI" pcb drc --severity-all --format json -o build/drc-logic.json \
    build/x68000-logic.kicad_pcb | grep Found
"$CLI" pcb export svg --layers F.Cu,B.Cu,In1.Cu,In2.Cu,F.SilkS,Edge.Cuts \
    --page-size-mode 2 --exclude-drawing-sheet \
    -o build/views/logic-routed.svg build/x68000-logic.kicad_pcb >/dev/null
echo "wrote build/x68000-logic.kicad_pcb"
