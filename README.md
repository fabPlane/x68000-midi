# X68000 MIDI

An expansion-slot card for the Sharp X68000 that puts a **standard 3.5 mm TRS-A MIDI OUT** (and IN) on the back of the machine, plus **8 MB of main-memory expansion**, on one PCB. Built to feed the [MIDI BOX](../midi_box) — or an original MT-32 / SC-55 — with a cable you can buy in any music shop instead of Sharp's mini-DIN lead.

Design brief and decisions: [midi_box/docs/x68000-midi.html](../midi_box/docs/x68000-midi.html) (rendered; open it from the midi_box docs server). Summary in [docs/DECISIONS.md](docs/DECISIONS.md).

## Layout

| Path | What |
|---|---|
| `hw/` | [`netlist.py`](hw/netlist.py) — rev A netlist, Gowin `.cst`, review report · [`breakout.py`](hw/breakout.py) — the universal slot breakout · KiCad layout (not started) |
| `mech/` | [`plate.py`](mech/plate.py) — the 176 × 20 × 2 mm slot cover plate · `hw/lib/x68k.pretty` — the X68000 2 × 50 edge-connector footprint, from midiori · `gw1nr9_qn88p_pins.csv` — the FPGA's pin/bank table, extracted from Gowin UG803 |
| `gateware/` | Amaranth: 68000 bus slave, YM3802 register file and transmitter, two-port card, a 68000 bus master for measuring, and a midiori reference model. 48 tests. PSRAM window to come |
| `mech/` | slot cover plate 176 × 20 × 2 mm (midiori's TRS plate, to be re-cut for 3–4 jacks) and the card-pull tool |
| `docs/` | [HARDWARE.md](docs/HARDWARE.md) — rev A specification · X68000 expansion-slot pinout · [DECISIONS.md](docs/DECISIONS.md) |
| `reference/midiori/` | verbatim copy of [tdaede/midiori](https://github.com/tdaede/midiori) at 67ee286 (2026-09-11), CC0 — the board and gateware this card starts from |

## Status

**Gateware**: the MIDI half is done and tested end to end — real 68000 bus cycles in, decoded MIDI
bytes out, including the driver probe sequence and an interrupt-acknowledge vector read. 48 pysim
tests. The PSRAM window is the remaining block.

**Hardware**: `python3 hw/netlist.py` generates `build/x68000-midi.net` (import into pcbnew),
`build/x68000-midi.cst` (the matching Gowin pin constraints) and `build/netlist-report.md`, with all
checks passing — 66 components, 133 nets, 60 of 63 usable FPGA I/O. [docs/HARDWARE.md](docs/HARDWARE.md) specifies rev A — the 1.8 V bank-3 constraint and
what it forces, the pin budget (45 of 48 at 3.3 V, 15 of 15 at 1.8 V), rails, BOM and fab notes.
Placement and routing have not been started and are the critical path; the netlist is their input.

**Slot breakout** (`python3 hw/breakout.py`): a dumb, universal card — the 2 × 50 fingers, six
translators and two 2 × 20 IDC headers, 37 components and 117 nets, all checks passing. It is the
cheap way to prove the edge-finger footprint, the 45° bevel and the card-guide fit before committing
to the big board, it puts the Tang Console on a real machine, and every later card (RAM, NE2000
Ethernet, Mercury-style PCM, SCSI, a bus analyser) prototypes on it. Build it buffered for a 3.3 V
host, or leave the translators off for a 5 V-tolerant one such as an RP2350.

Its placement is generated too — `hw/layout_breakout.py` (run with KiCad's own Python) writes a
DRC-clean `build/x68000-breakout.kicad_pcb` with every footprint placed and every pad on its net,
ready to route.

Next: route and order the breakout, Tang Console bring-up of the MIDI path over it, PSRAM
read-latency measurement on a Tang Nano 9K, then the main PCB.

## Licence

CC0, like midiori (see `LICENSE`).
