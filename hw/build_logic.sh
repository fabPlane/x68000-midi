#!/bin/sh
# Place, pour, route and check the logic board. Mirror of build_breakout.sh.
set -e
cd "$(dirname "$0")/.."
KPY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
CLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
JAR=${FREEROUTING_JAR:-/Users/hyper/projects/tensorfleet/freerouting-otel/build/libs/freerouting-current-executable.jar}
PASSES=${PASSES:-100}

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
for layer in ("In1.Cu", "In4.Cu"):   # In2/In3 stay signal layers
    t = re.sub(r"(\(layer %s\s*\(type )signal(\))" % re.escape(layer), r"\1power\2", t)
p.write_text(t)
print("marked In1.Cu and In4.Cu as power layers", flush=True)
PY
rm -f build/x68000-logic.ses
java -Djava.awt.headless=true -jar "$JAR" \
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
"$CLI" pcb drc --severity-all --format json -o build/drc-logic.json \
    build/x68000-logic.kicad_pcb | grep Found
"$CLI" pcb export svg --layers F.Cu,B.Cu,In1.Cu,In2.Cu,F.SilkS,Edge.Cuts \
    --page-size-mode 2 --exclude-drawing-sheet \
    -o build/views/logic-routed.svg build/x68000-logic.kicad_pcb >/dev/null
echo "wrote build/x68000-logic.kicad_pcb"
