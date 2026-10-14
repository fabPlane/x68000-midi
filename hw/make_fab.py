"""Build a JLCPCB-ready fab package for one board.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/make_fab.py logic
    ... hw/make_fab.py breakout

Produces, under `build/fab/<board>/`:

* gerbers and Excellon drill files, zipped as `<board>-gerbers.zip`
* `<board>-bom.csv` and `<board>-cpl.csv` in JLCPCB's column order, covering
  **only the parts the assembler fits**
* `<board>-hand-solder.csv`, the parts left for us, with the reason

Three things here are deliberate.

**The BOM and the CPL come from different places on purpose.** Part numbers
come from the netlist, which is the source of truth for what a part *is*;
positions and rotations come from the routed board, which is the source of
truth for where it *went*. Taking both from one place would mean trusting a
copy.

**DNP and hand-soldered parts are excluded from the CPL, not marked on it.**
JLCPCB's flow is "everything on this file gets placed", so a part that must not
be placed has to be absent. The hand-solder list is written separately so
nothing is silently dropped.

**Rotations are KiCad's, and KiCad's are not always JLCPCB's.** There is no way
to settle that from here: JLCPCB's own convention differs per package and is
only authoritative in their order preview, which shows each part on the board
and lets you rotate it. So this prints the parts whose orientation actually
matters -- the ones where a 180 degree error is a dead board rather than a
cosmetic nit -- and you check those in the preview.
"""

from __future__ import annotations

import csv
import importlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"

BOARDS = {
    "logic": dict(board="x68000-logic.kicad_pcb", module="logic", layers=4),
    "breakout": dict(board="x68000-breakout.kicad_pcb", module="breakout",
                     layers=4),
}

#: Nothing to buy: the card edge is the PCB itself, test points are bare pads.
SKIP_KINDS = {"EDGE", "TP"}

#: Parts where getting the rotation wrong is fatal rather than untidy, by the
#: kind in the netlist. Two-terminal passives are symmetric and left out.
POLARISED = {"FPGA", "LDO", "LDO3", "LVC2G07", "H11L1", "OSC", "LED", "DIODE",
             "IDC40", "JTAG", "DIP4", "TRS", "LVC8T245"}


def sort_key(ref: str):
    digits = re.sub(r"\D", "", ref)
    return (re.sub(r"\d", "", ref), int(digits or 0))


def run(args: list[str]) -> None:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{args[1:4]} failed:\n{r.stdout}\n{r.stderr}")


def gerbers(board_path: Path, out: Path, layers: int, stem: str) -> Path:
    names = ["F.Cu"] + [f"In{i}.Cu" for i in range(1, layers - 1)] + ["B.Cu"]
    names += ["F.Paste", "B.Paste", "F.Silkscreen", "B.Silkscreen",
              "F.Mask", "B.Mask", "Edge.Cuts"]
    gdir = out / "gerber"
    if gdir.exists():
        shutil.rmtree(gdir)
    gdir.mkdir(parents=True)
    run([CLI, "pcb", "export", "gerbers", "--layers", ",".join(names),
         "--no-protel-ext", "--subtract-soldermask", "-o", str(gdir),
         str(board_path)])
    run([CLI, "pcb", "export", "drill", "--format", "excellon",
         "--drill-origin", "absolute", "--excellon-units", "mm",
         "--generate-map", "--map-format", "gerberx2",
         "-o", str(gdir), str(board_path)])
    zpath = out / f"{stem}-gerbers.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(gdir.iterdir()):
            z.write(f, f.name)
    return zpath


def main(argv) -> int:
    name = argv[1] if len(argv) > 1 else "logic"
    try:
        cfg = BOARDS[name]
    except KeyError:
        raise SystemExit(f"unknown board {name!r}; "
                         f"expected one of {', '.join(sorted(BOARDS))}")

    board_path = ROOT / "build" / cfg["board"]
    if not board_path.exists():
        raise SystemExit(f"{board_path} not found — build that board first")
    stem = board_path.stem
    out = ROOT / "build" / "fab" / name
    out.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(ROOT / "hw"))
    mod = importlib.import_module(cfg["module"])
    from netlist import renumber
    built = mod.build()
    design = built[0] if isinstance(built, tuple) else built
    renumber(design)

    board = pcbnew.LoadBoard(str(board_path))
    placed = {f.GetReference(): f for f in board.GetFootprints()}

    # --- who fits what ----------------------------------------------------
    assemble, byhand, skipped = {}, {}, {}
    for ref, comp in design.comps.items():
        if comp["kind"] in SKIP_KINDS:
            continue
        if comp["dnp"]:
            skipped[ref] = comp
        elif comp.get("hand"):
            byhand[ref] = comp
        else:
            assemble[ref] = comp

    # --- BOM: grouped, part numbers from the netlist -----------------------
    groups: dict[tuple, list[str]] = {}
    for ref, comp in assemble.items():
        key = (comp["value"], comp["footprint"], comp["lcsc"])
        groups.setdefault(key, []).append(ref)
    bom = [{"Comment": v, "Designator": ",".join(sorted(r, key=sort_key)),
            "Footprint": fp.split(":")[-1], "LCSC Part #": lcsc,
            "Qty": len(r)}
           for (v, fp, lcsc), r in groups.items()]
    bom.sort(key=lambda r: (-r["Qty"], r["Comment"]))
    bom_path = out / f"{stem}-bom.csv"
    with bom_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Comment", "Designator", "Footprint",
                                          "LCSC Part #", "Qty"])
        w.writeheader(); w.writerows(bom)

    # --- CPL: positions and rotations from the routed board ----------------
    cpl, absent = [], []
    for ref in sorted(assemble, key=sort_key):
        fp = placed.get(ref)
        if fp is None:
            absent.append(ref)
            continue
        p = fp.GetPosition()
        cpl.append({"Designator": ref,
                    "Mid X": f"{pcbnew.ToMM(p.x):.4f}",
                    "Mid Y": f"{-pcbnew.ToMM(p.y):.4f}",
                    "Layer": "bottom" if fp.IsFlipped() else "top",
                    "Rotation": f"{fp.GetOrientationDegrees() % 360:.2f}"})
    cpl_path = out / f"{stem}-cpl.csv"
    with cpl_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Designator", "Mid X", "Mid Y",
                                          "Layer", "Rotation"])
        w.writeheader(); w.writerows(cpl)

    # --- what we solder ourselves -----------------------------------------
    hand_path = out / f"{stem}-hand-solder.csv"
    with hand_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Part", "Footprint", "Why not assembled"])
        for ref in sorted(byhand, key=sort_key):
            c = byhand[ref]
            w.writerow([ref, c["value"], c["footprint"].split(":")[-1],
                        c.get("hand_reason", "see docs/DECISIONS.md")])
        for ref in sorted(skipped, key=sort_key):
            c = skipped[ref]
            # A DNP part may still carry an instruction -- JP3 on the breakout
            # is DNP so a 3.3 V host cannot be destroyed by a live 5 V pin,
            # *and* has to be fitted for our own pair to power up. Saying only
            # "not fitted" would be true and misleading.
            w.writerow([ref, c["value"], c["footprint"].split(":")[-1],
                        c.get("hand_reason", "DNP — not fitted")])

    zpath = gerbers(board_path, out, cfg["layers"], stem)

    # --- report ------------------------------------------------------------
    print(f"{name}: {board.GetCopperLayerCount()} layers, "
          f"{len(design.comps)} components")
    print(f"  assembled by JLCPCB : {len(assemble)} parts, {len(bom)} BOM lines")
    print(f"  hand-soldered       : {len(byhand)}")
    print(f"  DNP                 : {len(skipped)}")
    no_lcsc = [r["Designator"] for r in bom if not r["LCSC Part #"]]
    if no_lcsc:
        print(f"  NO LCSC NUMBER      : {', '.join(no_lcsc)}")
    if absent:
        print(f"  IN THE BOM BUT NOT ON THE BOARD: {', '.join(absent)}")
    print(f"  wrote {zpath.name}, {bom_path.name}, {cpl_path.name}, "
          f"{hand_path.name}")

    check = sorted(r for r in assemble if assemble[r]["kind"] in POLARISED)
    print("\n  Check these rotations in JLCPCB's order preview — KiCad's")
    print("  convention and theirs differ per package, and for these a 180")
    print(f"  degree error is a dead board: {', '.join(check)}")
    return 1 if (no_lcsc or absent) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
