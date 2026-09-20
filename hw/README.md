# Hardware

KiCad project for rev A — not yet started. Footprints in `lib/x68k.pretty` (from midiori, CC0):

- `x68000_expansion` — 2 × 50 card-edge fingers, 2.54 mm pitch, 8 × 1.8 mm pads both sides, 124.46 mm long.
- `MD-50SM` — the mini-DIN-5 Sharp used; kept for reference, not used on this board.

Board outline and guide notches: copy from `reference/midiori/midiori.kicad_pcb` (150 × 141 mm, plate holes 136 mm apart).

Bus interface: 74LVC245A single-supply translators for inputs (as midiori v3.0), SN74LVC8T245 with VCCB = 1.8 V for the address lines that land on bank 3, and for D0–D15. MIDI IN circuit from `reference/midiori-v3.0/midiori.kicad_sch` (H11L1).

Rails: +5 V (slot) → 3.3 V (banks 1/2, translators, USB bridge, opto) → 1.8 V (VCCIO3 + PSRAM) → 1.2 V (core).
