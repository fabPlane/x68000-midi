"""Build docs/boards.html — every board in the project, on one page.

    python3 hw/make_overview.py

Self-contained: the renders and layer plots are inlined as data URIs, the way
the enclosure project's build guides do it, so the file can be opened or sent
anywhere without `build/` beside it.

Run `hw/build_breakout.sh` first — this reads what that produced.
"""

from __future__ import annotations

import base64
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VIEWS = ROOT / "build" / "views"
OUT = ROOT / "docs" / "boards.html"

LAYERS = [
    ("top", "Top copper", "F.Cu with silkscreen — data and control translators, "
     "the two ribbon headers, the open-drain returns."),
    ("bottom", "Bottom copper", "B.Cu — the address translators live here, so "
     "the address bundle never shares a layer with the data bundle."),
    ("in1-gnd", "In1 · ground", "A single GND pour shaped around the finger tab, "
     "plus surface pours on both outer layers."),
    ("in2-power", "In2 · power", "+5 V on the left, VREF on the right, split "
     "through the translator column so each supply pin sits over its own pour."),
]


def data_uri(path: pathlib.Path) -> str:
    mime = "image/png" if path.suffix == ".png" else "image/svg+xml"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def board_stats() -> dict:
    """Counts straight out of the board, so the page cannot drift from it."""
    kpy = ("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework"
           "/Versions/Current/bin/python3")
    script = """
import json, pcbnew
b = pcbnew.LoadBoard("build/x68000-breakout.kicad_pcb")
tracks = [t for t in b.Tracks() if t.Type() != pcbnew.PCB_VIA_T]
print(json.dumps({
    "tracks": len(tracks),
    "vias": len(list(b.Tracks())) - len(tracks),
    "footprints": len(list(b.GetFootprints())),
    "nets": b.GetNetCount() - 1,
    "zones": len(list(b.Zones())),
}))
"""
    out = subprocess.run([kpy, "-c", script], cwd=ROOT, capture_output=True,
                         text=True).stdout.strip().splitlines()
    return json.loads(out[-1])


def drc_summary() -> tuple[int, int, dict]:
    report = json.loads((ROOT / "build" / "drc-all.json").read_text())
    violations = report.get("violations", [])
    errors = sum(1 for v in violations if v.get("severity") == "error")
    kinds: dict[str, int] = {}
    for v in violations:
        kinds[v["type"]] = kinds.get(v["type"], 0) + 1
    return errors, len(report.get("unconnected_items", [])), kinds


def plate_svg() -> str:
    """The cover plate, drawn from mech/plate.py's own constants."""
    sys.path.insert(0, str(ROOT / "mech"))
    import plate  # noqa: E402

    jacks = "".join(
        f'<circle cx="{x:.2f}" cy="{plate.OPENING_Y}" r="{plate.JACK_D / 2}" '
        f'class="cut"/>' for x in plate.jack_x())
    holes = "".join(
        f'<circle cx="{x}" cy="{plate.M3_Y}" r="{plate.M3 / 2}" class="cut"/>'
        for x in plate.M3_X)
    labels = "".join(
        f'<text x="{x:.2f}" y="{plate.OPENING_Y - 5.2}" text-anchor="middle" '
        f'class="plabel">{name}</text>'
        for name, x in zip(plate.JACK_LABELS, plate.jack_x()))
    outline = (f"M0,{plate.HEIGHT} L0,{plate.STEP_DEPTH} "
               f"L{plate.STEP_X0},{plate.STEP_DEPTH} L{plate.STEP_X0},0 "
               f"L{plate.STEP_X1},0 L{plate.STEP_X1},{plate.STEP_DEPTH} "
               f"L{plate.LEN},{plate.STEP_DEPTH} L{plate.LEN},{plate.HEIGHT} Z")
    return (f'<svg viewBox="-2 -2 {plate.LEN + 4} {plate.HEIGHT + 4}" '
            f'role="img" aria-label="Slot cover plate">'
            f'<path d="{outline}" class="plate"/>{jacks}{holes}{labels}</svg>')


def main() -> int:
    errors, unconnected, kinds = drc_summary()
    stats = board_stats()
    renders = {
        "top": data_uri(VIEWS / "breakout-3d.png"),
        "bottom": data_uri(VIEWS / "breakout-3d-bottom.png"),
    }
    layers = {key: data_uri(VIEWS / f"breakout-{key}.svg")
              for key, _, _ in LAYERS}

    warn_line = ", ".join(f"{n} {k.replace('_', ' ')}"
                          for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))

    buttons = "".join(
        f'<button data-layer="{key}"{" class=on" if i == 0 else ""}>{name}</button>'
        for i, (key, name, _) in enumerate(LAYERS))
    panes = "".join(
        f'<figure class="pane" data-layer="{key}"{"" if i == 0 else " hidden"}>'
        f'<img src="{layers[key]}" alt="{name}"><figcaption>{note}</figcaption>'
        f'</figure>' for i, (key, name, note) in enumerate(LAYERS))

    html = f"""<meta charset="utf-8"><title>X68000 MIDI Boards</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{
  --bg:#F1EEE7; --ink:#1E1D1B; --muted:#6F6A61; --line:#D8D2C6;
  --panel:#FFFFFF; --panel2:#E9E5DC; --amber:#F28C1E; --ok:#3B8A4E;
  --warn:#B9541B; --bad:#B03A2E;
  --display:"Barlow Condensed","Arial Narrow",Helvetica,Arial,sans-serif;
  --body:"IBM Plex Sans","Helvetica Neue",Arial,sans-serif;
  --mono:"IBM Plex Mono",Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{
  --bg:#1A1A1D; --ink:#ECE8DF; --muted:#9C978C; --line:#35353B;
  --panel:#232327; --panel2:#2C2C31;}}}}
:root[data-theme=dark]{{--bg:#1A1A1D;--ink:#ECE8DF;--muted:#9C978C;
  --line:#35353B;--panel:#232327;--panel2:#2C2C31;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 var(--body);-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1040px;margin:0 auto;padding-block:28px 64px;padding-inline:20px}}
header{{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:end;
  border-bottom:3px solid var(--ink);padding-bottom:16px}}
.brand{{font:700 50px/0.95 var(--display);letter-spacing:.02em;
  text-transform:uppercase;margin:0}}
.brand span{{color:var(--amber)}}
.kicker{{font:600 13px/1 var(--display);letter-spacing:.18em;
  text-transform:uppercase;color:var(--muted);margin:0 0 10px}}
.lcd{{background:var(--amber);color:#3A1F06;font:500 13px/1.5 var(--mono);
  padding:10px 14px;border-radius:4px;min-width:250px;
  box-shadow:inset 0 0 0 2px #0002}}
.lcd b{{display:block;font-size:18px}}
.lcd .row{{display:flex;justify-content:space-between;gap:16px}}
h2{{font:700 28px/1.05 var(--display);letter-spacing:.03em;
  text-transform:uppercase;margin:44px 0 6px}}
h2 small{{font:600 12px/1 var(--display);letter-spacing:.18em;color:var(--amber);
  display:block;margin-bottom:6px}}
p{{max-width:70ch;margin:8px 0}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
  gap:16px;margin:16px 0}}
figure{{margin:0;background:var(--panel2);border-radius:8px;overflow:hidden}}
figure img{{display:block;width:100%;height:auto;background:#fff}}
/* A 76 x 150 mm board at full page width is 2000 px tall; cap it so a layer
   fits on screen and the detail is a zoom away rather than a scroll away. */
.pane{{display:flex;flex-direction:column;align-items:center;background:var(--panel2)}}
.pane[hidden]{{display:none}}   /* display:flex beats the default [hidden] rule */
.pane img{{width:auto;max-width:100%;max-height:78vh;margin:0 auto}}
.pane figcaption{{align-self:stretch}}
figcaption{{font:500 12px/1.45 var(--mono);color:var(--muted);padding:9px 12px}}
.tabs{{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 10px}}
.tabs button{{font:600 12px/1 var(--display);letter-spacing:.12em;
  text-transform:uppercase;padding:9px 13px;border:1px solid var(--line);
  background:var(--panel);color:var(--muted);border-radius:4px;cursor:pointer}}
.tabs button.on{{background:var(--amber);color:#3A1F06;border-color:var(--amber)}}
table{{width:100%;border-collapse:collapse;font-size:14px;margin:12px 0}}
.tbl{{overflow-x:auto}}
th{{text-align:left;font:600 11px/1.2 var(--display);letter-spacing:.14em;
  text-transform:uppercase;color:var(--muted);padding:8px 10px;
  border-bottom:2px solid var(--ink)}}
td{{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
td.num{{font-variant-numeric:tabular-nums;white-space:nowrap}}
code{{font:500 13px var(--mono);background:var(--panel2);padding:1px 5px;
  border-radius:3px}}
.pill{{display:inline-block;font:600 11px/1 var(--display);letter-spacing:.1em;
  text-transform:uppercase;padding:4px 8px;border-radius:3px;
  background:var(--panel2);color:var(--muted)}}
.pill.ok{{background:#3B8A4E22;color:var(--ok)}}
.pill.warn{{background:#B9541B22;color:var(--warn)}}
.pill.bad{{background:#B03A2E22;color:var(--bad)}}
.callout{{border-left:4px solid var(--amber);background:var(--panel);
  padding:12px 16px;border-radius:0 6px 6px 0;margin:16px 0}}
.callout b{{font:600 13px/1 var(--display);letter-spacing:.14em;
  text-transform:uppercase;color:var(--muted);display:block;margin-bottom:6px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:16px 18px}}
.card h3{{font:600 19px/1.2 var(--display);letter-spacing:.03em;
  text-transform:uppercase;margin:0 0 4px}}
.plate{{fill:var(--panel2);stroke:var(--ink);stroke-width:0.6}}
.cut{{fill:var(--bg);stroke:var(--amber);stroke-width:0.6}}
.plabel{{font:600 4px var(--display);letter-spacing:.1em;fill:var(--muted)}}
footer{{margin-top:48px;border-top:1px solid var(--line);padding-top:12px;
  color:var(--muted);font-size:13px}}
a{{color:inherit}}
@media (max-width:640px){{header{{grid-template-columns:1fr}}.brand{{font-size:38px}}}}
</style>
<div class="wrap">
<header>
  <div>
    <p class="kicker">fabPlane · x68000_midi</p>
    <h1 class="brand">The <span>Boards</span></h1>
    <p class="kicker" style="margin:8px 0 0">Everything with copper on it, as it stands today</p>
  </div>
  <div class="lcd">
    <b>SLOT BREAKOUT</b>
    <div class="row"><span>DRC</span><span>{errors} errors</span></div>
    <div class="row"><span>UNCONNECTED</span><span>{unconnected}</span></div>
    <div class="row"><span>TRACKS</span><span>{stats['tracks']}</span></div>
  </div>
</header>

<h2><small>01</small>Slot breakout <span class="pill ok">routed</span></h2>
<p>The whole X68000 expansion bus on two ribbon cables: 2 × 50 gold fingers,
six SN74LVC8T245 level translators, two 2 × 20 IDC headers. Plug an FPGA or MCU
board on the other end and it becomes any expansion card you like. Built first
because it proves the three mechanical unknowns — the finger footprint, the 45°
bevel and the card-guide fit — for a few dollars.</p>

<div class="grid2">
  <figure><img src="{renders['top']}" alt="Breakout, top">
    <figcaption>Top. Fingers left, translators in a column, the two ribbons right.</figcaption></figure>
  <figure><img src="{renders['bottom']}" alt="Breakout, bottom">
    <figcaption>Bottom. The address translators sit here, under the others.</figcaption></figure>
</div>

<div class="tabs">{buttons}</div>
{panes}

<div class="tbl"><table>
<tr><th>Property</th><th class="num">Value</th><th>Note</th></tr>
<tr><td>Size</td><td class="num">76 × 150 mm</td><td>150 mm is fixed by the slot; the depth is ours</td></tr>
<tr><td>Stackup</td><td class="num">4 layer</td><td>signal · GND · +5 V/VREF · signal</td></tr>
<tr><td>Footprints</td><td class="num">{stats['footprints']}</td><td>29 parts to fit, 2 DNP</td></tr>
<tr><td>Nets</td><td class="num">{stats['nets']}</td><td>every signal net routed</td></tr>
<tr><td>Tracks / vias</td><td class="num">{stats['tracks']} / {stats['vias']}</td><td>freerouting, then plane stitching</td></tr>
<tr><td>Copper pours</td><td class="num">{stats['zones']}</td><td>GND on In1 and both outer layers; +5 V and VREF share In2</td></tr>
<tr><td>DRC</td><td class="num">{errors} errors</td><td>{warn_line}</td></tr>
<tr><td>Unconnected</td><td class="num">{unconnected}</td><td>all GND: one ground finger and the pour islands</td></tr>
</table></div>

<div class="callout"><b>Not ready to order</b>
{unconnected} GND connections still need a via each, placed by hand in pcbnew with
the ratsnest showing. Beyond that: there is no schematic, so ERC has never run;
the finger geometry is copied from midiori and has never been in a real slot;
hard gold and the bevel are order notes that are not in the gerbers; and the
BOM's LCSC column is deliberately empty. The full list is in
<code>docs/FAB-BREAKOUT.md</code>.</div>

<h2><small>02</small>X68000 MIDI card <span class="pill warn">netlist only</span></h2>
<div class="grid2">
  <div class="card">
    <h3>What it is</h3>
    <p>Two MIDI ports on 3.5 mm TRS-A jacks at <code>$EAFA00</code> and
    <code>$EAFA10</code>, plus 8 MB of main-memory expansion, on one card —
    a Gowin GW1NR-9 with its PSRAM in the package doing all of it.</p>
    <p>The gateware is finished and tested: real 68000 bus cycles in, decoded
    MIDI bytes out, 48 pysim tests including a driver probe sequence and an
    interrupt-acknowledge vector read.</p>
  </div>
  <div class="card">
    <h3>Where it stands</h3>
    <div class="tbl"><table>
      <tr><td>Netlist</td><td class="num">66 parts, 134 nets</td></tr>
      <tr><td>FPGA pins</td><td class="num">60 of 63 used</td></tr>
      <tr><td>Gateware</td><td class="num">48 tests passing</td></tr>
      <tr><td>Layout</td><td class="num">not started</td></tr>
    </table></div>
    <p>Bank 3 of the QN88P must run at 1.8 V because it carries the PSRAM, and
    JTAG lives there — which splits the address bus 15 lines at 1.8 V and 8 at
    3.3 V, and means the programmer has to drive 1.8 V.</p>
  </div>
</div>

<h2><small>03</small>Slot cover plate <span class="pill ok">drawn</span></h2>
<p>176 × 20 × 2 mm, outline taken from midiori's — a known-good fit — with the
hole sizes and positions measured out of their STL rather than guessed. Three
3.5 mm bores at 22 mm pitch; the middle one sits exactly where theirs does, so
that one's clearance is proven.</p>
<figure style="background:var(--panel);padding:18px">{plate_svg()}
<figcaption>OUT · OUT 2 · IN. Generated by <code>mech/plate.py</code>, which also
writes the STL and the DXF.</figcaption></figure>

<footer>
Generated by <code>hw/make_overview.py</code> from the current build —
rerun <code>hw/build_breakout.sh</code> and this page follows.
Reference design: <a href="https://github.com/tdaede/midiori">tdaede/midiori</a> (CC0).
</footer>
</div>
<script>
document.querySelectorAll('.tabs button').forEach(b => {{
  b.onclick = () => {{
    document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    document.querySelectorAll('.pane').forEach(p => {{
      p.hidden = p.dataset.layer !== b.dataset.layer;
    }});
  }};
}});
</script>
"""
    OUT.write_text(html)
    size = OUT.stat().st_size / 1e6
    print(f"wrote {OUT} ({size:.1f} MB, self-contained)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
