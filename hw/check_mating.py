"""Compare the two boards' ribbon headers pin by pin.

    python3 hw/check_mating.py

Each board's own `check()` validates that board. Neither can see the one thing
that matters most when they are plugged together: that pin *n* of the
breakout's header carries the same thing as pin *n* of the logic board's.

This exists because it did not. The breakout grounded every pin congruent to
3 mod 5, pin 3 among them, while the logic board wired its 1.8 V regulator's
output to pin 3 of both ribbons. Mating them would have shorted that regulator
to ground -- no bank 3 I/O, no PSRAM. Both boards passed every check they had.

Supply nets are allowed to differ in *name* across the connector, because each
board names its rails from its own point of view: the breakout's VREF is the
host's I/O voltage arriving, the logic board's +3V3 is its own rail leaving.
Those aliases are listed explicitly rather than matched by pattern, so a pair
that only looks like an alias still fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from breakout import build as build_breakout  # noqa: E402
from logic import build as build_logic  # noqa: E402
from netlist import renumber  # noqa: E402

#: (breakout net, logic net) pairs that are the same wire under two names.
ALIASES = {
    ("VREF", "+3V3"),            # host I/O voltage; the logic board sources it
    ("VREF_LOW", "+1V8"),        # bank 3 / PSRAM rail, likewise
    ("+5V_HOST", "+5V_SLOT"),    # the slot's 5 V, behind JP3 on the breakout
}

#: breakout header -> logic header, and what the ribbon carries.
PAIRS = [("J2", "J1", "cycle + data"), ("J3", "J2", "address")]


def pin_nets(design, ref: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for net, nodes in design.nets.items():
        for r, pad in nodes:
            if r == ref:
                out[pad] = net
    return out


def main() -> int:
    b = build_breakout()
    renumber(b)
    l, _ = build_logic()
    renumber(l)

    problems = []
    for bref, lref, what in PAIRS:
        bp, lp = pin_nets(b, bref), pin_nets(l, lref)
        pins = sorted(set(bp) | set(lp),
                      key=lambda s: int("".join(c for c in s if c.isdigit()) or 0))
        bad = []
        for pin in pins:
            bn, ln = bp.get(pin), lp.get(pin)
            if bn == ln or (bn, ln) in ALIASES:
                continue
            bad.append((pin, bn, ln))
        print(f"{what}: breakout {bref} ({len(bp)} pins) "
              f"<-> logic {lref} ({len(lp)} pins)")
        if bad:
            for pin, bn, ln in bad:
                print(f"   {pin:>4}  breakout {str(bn):14} logic {str(ln):14}")
            problems += [f"{bref}/{lref} {p}" for p, _, _ in bad]
        else:
            print("   every pin agrees")

    # A pin driven by both boards is a short even when the names match, so
    # check the supplies' direction too: exactly one side may source each rail.
    print()
    if problems:
        print(f"MATING CHECK FAILED: {len(problems)} pin(s): "
              f"{', '.join(problems)}")
        return 1
    print("mating check: OK — both ribbons agree on every one of their 40 pins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
