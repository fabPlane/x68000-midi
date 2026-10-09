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
java -Djava.awt.headless=true -jar "$JAR" \
    -de build/x68000-breakout.dsn -do build/x68000-breakout.ses \
    -mp "$PASSES" -mt 4 2>&1 | grep -E "Auto-routing stage completed|Optimization stage completed" || true
"$KPY" - <<'PY'
import pcbnew
b = pcbnew.LoadBoard("build/x68000-breakout.kicad_pcb")
assert pcbnew.ImportSpecctraSES(b, "build/x68000-breakout.ses")
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
pcbnew.SaveBoard("build/x68000-breakout.kicad_pcb", b)
print("imported session, zones filled", flush=True)
PY
"$CLI" pcb drc --severity-error --format json -o build/drc.json \
    build/x68000-breakout.kicad_pcb | grep Found
"$CLI" pcb export svg --layers F.Cu,B.Cu,In1.Cu,In2.Cu,F.SilkS,Edge.Cuts \
    --page-size-mode 2 --exclude-drawing-sheet \
    -o build/breakout-routed.svg build/x68000-breakout.kicad_pcb >/dev/null
echo "wrote build/x68000-breakout.kicad_pcb and build/breakout-routed.svg"
