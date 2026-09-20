midiori tag `v3.0` (63e8663, 2024-12-10) — an unreleased hardware revision on a side branch of
https://github.com/tdaede/midiori (CC0). Hardware only; the gateware is v2's.

What it adds over the v2.2 board in `../midiori/`:
- MIDI IN via an H11L1 opto (plus a second mini-DIN); "add bus mastering and midi in".
- 8 × 74LVC245A single-supply translators instead of the dual-supply LVC8T245s.
- An AK4432 audio DAC with TL072/TL074 output stages on ±12 V from the slot and four 3.5 mm audio jacks — an on-card audio/PCM output.
- iCE40HX1K in TQ144, KiCad 6+ file format.

Kept here for the MIDI IN circuit, the LVC245 bus interface and as a KiCad 6 starting point.
