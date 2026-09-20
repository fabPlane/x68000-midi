# Decisions

Dated log; the reasoning is in the brief (`midi_box/docs/x68000-midi.html`).

## 2026-09-20 — rev A scope

| Topic | Decision |
|---|---|
| Connector | 3.5 mm TRS **Type A** (MMA RP-054): tip = DIN 5 (sink), ring = DIN 4 (source), sleeve = DIN 2. Fits the 20 mm slot plate; stock TRS-A → DIN-5 cables. |
| Functions | MIDI OUT · MIDI IN (jack + opto populated, receive gateware later) · 8 MB RAM · second OUT at `$EAFA10` as a DNP jack |
| FPGA | Gowin **GW1NR-LV9QN88PC6/I5** (JLCPCB C5799578): 8640 LUT, 64 Mbit PSRAM in package. RAM is a bitstream + DIP option on one PCB. |
| Bus interface | as midiori: level shifters on the 5 V slot side (LVC8T245 for D0–D15, 74LVC245 for A1–A23 and controls), open-drain ~DTACK / ~IRQ. IRQ2/4 + IACK by jumper. |
| Address map | MIDI `$EAFA00–0F` (+`$EAFA10–1F` for OUT 2); RAM 8 MB at base 1 / 2 / 4 MB by DIP (→ 9 / 10 / 12 MB total) |
| MIDI OUT driver | 5 V loop, 220 Ω / 220 Ω, 74LVC1G07 open drain (midiori's circuit) |
| MIDI IN | H11L1 + 220 Ω + 1N4148, output pulled to 3.3 V |
| Clock | 27 MHz oscillator (27 MHz / 864 = 31 250 bps exactly); PLL for the PSRAM controller |
| Programming | **onboard USB-C on the plate**: JTAG + debug UART (FT232H-class) — cards reflash in situ |
| Board | 150 × 141 mm (midiori's outline and guide notches kept for rev A), 4-layer, hard-gold bevelled fingers; 176 × 20 × 2 mm plate |
| Assembly | JLCPCB SMT, run of 5 |
| First target machine | the original X68000 (CZ-600C, 10 MHz, 1 MB) — hence the 1 MB RAM-base option |
| Gateware | Amaranth (port of midiori's Migen core), open toolchain yosys + nextpnr-himbaechel + apicula; Gowin EDA as fallback |
| Name | **X68000 MIDI** |
| Not on this card | SCSI (own card later) · USB inputs (front-port dongle, tabled: `midi_box/docs/X68000-INPUT-DONGLE.md`) · Ethernet, Mercury-style PCM (v2 candidates) |

## Open — verify before drawing

- QN88P user-I/O count (~63?) and the PSRAM supply rail — Gowin DS117 / Tang Nano 9K schematic.
- Slot +5 V budget per card vs ~300 mA draw — *Outside X68000*.
- IPL memory sizing with a window at `$100000` on a 1 MB machine (contiguity), and at `$200000` on 2 MB.
- apicula support for the GW1NR-9 PSRAM ports (else Gowin EDA for that block).
- midiori ERRATA for the revision copied here.
