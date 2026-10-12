"""Bring any track freerouting drew below the board minimum back up to it.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/widen_thin.py build/x68000-logic.kicad_pcb

Freerouting routes to the DSN's width rule but will narrow a track where it
thinks it has to. On the logic board that left 27 tracks at 0.112 mm — under the
0.15 mm board minimum, and under JLCPCB's standard 0.127 mm capability, so they
would have to be paid for with a finer process.

Widening is only safe if the extra copper still clears its neighbours, so this
re-runs DRC afterwards: if clearance errors appear, the right answer is a finer
process, not a wider track.
"""

import sys
from pathlib import Path

import pcbnew


def main(argv) -> int:
    path = Path(argv[1])
    board = pcbnew.LoadBoard(str(path))
    minimum = board.GetDesignSettings().m_TrackMinWidth
    # Widen, then put back any that no longer clears its neighbours. One pass
    # of DRC decides it: on the logic board 26 of 27 widened safely and one did
    # not, which is a cheaper answer than paying for a finer process board-wide.
    narrow = [t for t in board.Tracks()
              if t.Type() != pcbnew.PCB_VIA_T and t.GetWidth() < minimum]
    original = {t.m_Uuid.AsString(): t.GetWidth() for t in narrow}
    for t in narrow:
        t.SetWidth(minimum)
    pcbnew.SaveBoard(str(path), board)

    import json
    import subprocess
    cli = ("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
    report = path.parent / "drc-widen.json"
    subprocess.run([cli, "pcb", "drc", "--severity-error", "--format", "json",
                    "-o", str(report), str(path)], capture_output=True)
    bad = set()
    for v in json.loads(report.read_text()).get("violations", []):
        if v["type"] == "clearance":
            for item in v.get("items", []):
                bad.add(item.get("uuid"))
    reverted = 0
    if bad:
        board = pcbnew.LoadBoard(str(path))
        for t in board.Tracks():
            uuid = t.m_Uuid.AsString()
            if uuid in bad and uuid in original:
                t.SetWidth(original[uuid])
                reverted += 1
        pcbnew.SaveBoard(str(path), board)
    print(f"widened {len(narrow)} tracks to {pcbnew.ToMM(minimum)} mm, "
          f"reverted {reverted} that no longer cleared")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
