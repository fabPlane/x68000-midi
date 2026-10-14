"""Bring any track freerouting drew below the board minimum back up to it.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
        hw/widen_thin.py build/x68000-logic.kicad_pcb

Freerouting routes to the DSN's width rule but will narrow a track where it
thinks it has to. On the logic board that left 27 tracks at 0.112 mm — under the
0.15 mm board minimum, and under JLCPCB's standard 0.127 mm capability, so they
would have to be paid for with a finer process.

Widening is only safe if the extra copper still clears its neighbours, so this
re-runs DRC after each step and walks down a ladder: 0.15 mm first, then 0.14,
0.13 and finally JLCPCB's standard 0.127 mm. Only a track that cannot hold even
0.127 mm needs a finer process; the earlier version reverted straight to what
freerouting drew (0.112 mm), which left a board-minimum error behind.
"""

import json
import subprocess
import sys
from pathlib import Path

import pcbnew

CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
#: Widest first. The last rung is JLCPCB's standard track width, so a track
#: that still does not clear there is the only one that costs a finer process.
LADDER = [0.15, 0.14, 0.13, 0.127]


def clearance_offenders(path: Path) -> set:
    report = path.parent / "drc-widen.json"
    subprocess.run([CLI, "pcb", "drc", "--severity-error", "--format", "json",
                    "-o", str(report), str(path)], capture_output=True)
    bad = set()
    for v in json.loads(report.read_text()).get("violations", []):
        if v["type"] == "clearance":
            for item in v.get("items", []):
                bad.add(item.get("uuid"))
    return bad


def main(argv) -> int:
    path = Path(argv[1])
    board = pcbnew.LoadBoard(str(path))
    # Anything under the design intent is a candidate, not just anything under
    # the board minimum: the board minimum is now the fab's floor (0.127 mm),
    # so a track sitting exactly on it is legal but still worth a try at 0.15.
    target = board.GetDesignSettings().m_NetSettings \
                  .GetDefaultNetclass().GetTrackWidth()
    narrow = [t.m_Uuid.AsString() for t in board.Tracks()
              if t.Type() != pcbnew.PCB_VIA_T and t.GetWidth() < target]
    settled: dict[str, float] = {}
    pending = set(narrow)

    for width in LADDER:
        if not pending:
            break
        board = pcbnew.LoadBoard(str(path))
        for t in board.Tracks():
            if t.m_Uuid.AsString() in pending:
                t.SetWidth(pcbnew.FromMM(width))
        pcbnew.SaveBoard(str(path), board)
        bad = clearance_offenders(path) & pending
        for uuid in pending - bad:
            settled[uuid] = width
        pending = bad
        if bad:
            # Put the ones that did not clear back where they were before the
            # next, narrower rung, so this rung's failures do not mask the next
            # rung's clearances.
            board = pcbnew.LoadBoard(str(path))
            for t in board.Tracks():
                if t.m_Uuid.AsString() in bad:
                    t.SetWidth(pcbnew.FromMM(LADDER[-1]))
            pcbnew.SaveBoard(str(path), board)

    for width in LADDER:
        n = sum(1 for w in settled.values() if w == width)
        if n:
            print(f"  {n} track(s) at {width} mm")
    print(f"widened {len(settled)} of {len(narrow)} narrow tracks; "
          f"{len(pending)} need a finer process than {LADDER[-1]} mm")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
