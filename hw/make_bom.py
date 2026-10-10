"""Write a JLCPCB-shaped BOM for the breakout, from the netlist.

    python3 hw/make_bom.py        # -> build/fab/x68000-breakout-bom.csv

There is no schematic to run `kicad-cli sch export bom` against — the netlist
*is* the source — so the BOM comes from the same component table the netlist
does, which is the only way the two cannot disagree.

**The LCSC column is deliberately empty.** JLCPCB assembly matches parts by
LCSC number against live stock, and a wrong number buys the wrong part. Fill it
in from JLCPCB's own parts search at order time, where you can see stock and
basic-vs-extended (extended parts carry a per-type loading fee). The comment
column carries the exact part to search for.
"""

from __future__ import annotations

import collections
import csv
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from breakout import build  # noqa: E402
from netlist import renumber  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "build" / "fab"

#: Not purchasable: the card edge is the PCB, test pads are bare copper.
SKIP_KINDS = {"EDGE", "TP"}

#: What to actually order, where the netlist value is a description.
PART = {
    "2x20 IDC -> host: cycle + data": "IDC box header, 2x20, 2.54 mm, vertical",
    "2x20 IDC -> host: address + spare": "IDC box header, 2x20, 2.54 mm, vertical",
    "0R link: local 3.3 V -> VREF": "0 R jumper, 0603",
    "polyfuse 500mA": "PTC resettable fuse, 500 mA hold, 0603",
}


def sort_key(ref: str):
    digits = re.sub(r"\D", "", ref)
    return (re.sub(r"\d", "", ref), int(digits or 0))


def main() -> int:
    design = build()
    renumber(design)

    groups = collections.defaultdict(list)
    for ref, comp in design.comps.items():
        if comp["kind"] in SKIP_KINDS:
            continue
        key = (PART.get(comp["value"], comp["value"]), comp["footprint"],
               comp["dnp"])
        groups[key].append(ref)

    rows = []
    for (value, footprint, dnp), refs in groups.items():
        rows.append({
            "Comment": value,
            "Designator": ",".join(sorted(refs, key=sort_key)),
            "Footprint": footprint.split(":")[1],
            "LCSC Part #": "",
            "Qty": len(refs),
            "Populate": "DNP" if dnp else "yes",
        })
    rows.sort(key=lambda r: (r["Populate"], -r["Qty"], r["Comment"]))

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "x68000-breakout-bom.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Comment", "Designator", "Footprint",
                                          "LCSC Part #", "Qty", "Populate"])
        w.writeheader()
        w.writerows(rows)

    fitted = sum(r["Qty"] for r in rows if r["Populate"] == "yes")
    print(f"{len(rows)} lines, {fitted} parts to fit "
          f"({sum(r['Qty'] for r in rows) - fitted} DNP)")
    for r in rows:
        print(f"  {r['Qty']:>3}  {r['Comment']:<42} {r['Populate']}")
    print(f"wrote {path} — the LCSC column is blank on purpose")
    return 0


if __name__ == "__main__":
    sys.exit(main())
