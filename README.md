# X68000 MIDI

An expansion-slot card for the Sharp X68000 that puts a **standard 3.5 mm TRS-A MIDI OUT** (and IN) on the back of the machine, plus **8 MB of main-memory expansion**, on one PCB. Built to feed the [MIDI BOX](../midi_box) — or an original MT-32 / SC-55 — with a cable you can buy in any music shop instead of Sharp's mini-DIN lead.

Design brief and decisions: [midi_box/docs/x68000-midi.html](../midi_box/docs/x68000-midi.html) (rendered; open it from the midi_box docs server). Summary in [docs/DECISIONS.md](docs/DECISIONS.md).

## Layout

| Path | What |
|---|---|
| `hw/` | KiCad project (rev A, to come) · `hw/lib/x68k.pretty` — the X68000 2 × 50 edge-connector footprint and the mini-DIN, from midiori |
| `gateware/` | Amaranth port of midiori's YM3802 transmit core for the Gowin GW1NR-9, plus the PSRAM window (to come) |
| `mech/` | slot cover plate 176 × 20 × 2 mm (midiori's TRS plate, to be re-cut for 3–4 jacks) and the card-pull tool |
| `docs/` | X68000 expansion-slot pinout; decisions |
| `reference/midiori/` | verbatim copy of [tdaede/midiori](https://github.com/tdaede/midiori) at 67ee286 (2026-09-11), CC0 — the board and gateware this card starts from |

## Status

Rev A not yet drawn. Next: Tang Nano 9K bring-up of the transmit core, then PSRAM read-latency measurement, then the PCB.

## Licence

CC0, like midiori (see `LICENSE`).
