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
| Rails | +5 V (slot) → 3.3 V (banks 1/2, translators, USB bridge, opto) → **1.8 V (VCCIO3 + PSRAM, confirmed 2026-09-20)** → 1.2 V (core). Three LDOs. |
| Clock | 27 MHz oscillator (27 MHz / 864 = 31 250 bps exactly); PLL for the PSRAM controller |
| Programming | **onboard USB-C on the plate**: JTAG + debug UART (FT232H-class) — cards reflash in situ |
| Board | 150 × 141 mm (midiori's outline and guide notches kept for rev A), 4-layer, hard-gold bevelled fingers; 176 × 20 × 2 mm plate |
| Assembly | JLCPCB SMT, run of 5 |
| First target machine | the original X68000 (CZ-600C, 10 MHz, 1 MB) — hence the 1 MB RAM-base option |
| Gateware | Amaranth (port of midiori's Migen core), open toolchain yosys + nextpnr-himbaechel + apicula; Gowin EDA as fallback |
| Name | **X68000 MIDI** |
| Not on this card | SCSI (own card later) · USB inputs (front-port dongle, tabled: `midi_box/docs/X68000-INPUT-DONGLE.md`) · Ethernet, Mercury-style PCM (v2 candidates) |

## 2026-09-20 — verified

| Item | Result | Consequence |
|---|---|---|
| QN88P user I/O | **71** (Gowin DS117 table 1-3, UG803 pin table). Bank 1: 25, bank 2: 23, bank 3: 22 (JTAG, RECONFIG_N, MODE and a GCLK among them). | — |
| PSRAM rail | **VCCIO3 must be 1.71–1.89 V**: bank 3 both powers the embedded PSRAM and is its interface (UG803 "Recommended Operating Conditions of QN88P"). PSRAM: 2 × 32 Mbit, 8-bit DDR each, 166 MHz, RWDS. | ~48 pins at 3.3 V + ~17 spare bank-3 pins at **1.8 V**. Our 60-pin budget fits only by putting A1–A16 through LVC8T245s with VCCB = 1.8 V into bank 3. Adds a 1.8 V LDO; the USB-JTAG bridge must run 1.8 V I/O (FT232H VCCIO 1.8 V is fine). The Tang Nano 9K runs bank 3 at 1.8 V too, so prototypes match. |
| Slot +5 V budget | not documented anywhere reachable. Nereid (16 MB DRAM + NIC + USB host) ran in the same slot. | ~300 mA is safe; note that original-X68000 PSUs are 35-year-old and weak. |
| IPL memory sizing | not confirmed online. `SWITCH.X MEMORY=` sets the size in SRAM; Polykubos requires ≥ 2 MB on board (no 1 MB base). | keep DIP bases 1 / 2 / 4 MB; test 1 MB base on the stock machine first — it may need to be 2 MB. |
| apicula | Tang Nano 9K / GW1NR-LV9QN88PC6/I5 is a listed target. Embedded-memory bond pads are documented only for the SDRAM LQ144P variant (`doc/sdram.md`); the QN88P PSRAM pads are not. | derive PSRAM IOB locations the way apicula did for SDRAM (one vendor-tool run with the magic port names, read the placement), then constrain them under nextpnr; fallback: build the PSRAM block, or the whole design, with Gowin EDA (free licence). |
| midiori revision | `reference/midiori/` = board **2.2** silkscreen, tag `v2.2.1` + one gateware fix (Overtake, Mahou Daisakusen). ERRATA lists v2.1 issues only. A side tag **v3.0** (2024-12, unreleased) adds MIDI IN (H11L1), 74LVC245A translators, an AK4432 audio DAC on ±12 V — copied to `reference/midiori-v3.0/`. | take the MIDI IN circuit and the LVC245 interface from v3.0; the v3.0 audio stage is the natural donor for a v2 PCM mix-in. |

## Open

- Confirm on the machine which RAM base the IPL accepts on a 1 MB X68000.
- Amaranth `GowinPlatform` + apicula build of a blinky on the Nano 9K before porting anything.

## 2026-10-09 — prior art found; scope corrections

A photo of a **XM-6BE10 + midiori** (えくしみえむ, [BOOTH](https://booth.pm/ja/items/5013636), ¥27,000, sold out) — this card, shipping: one I/O slot, 10 MB + MIDI OUT, Lattice FPGA + SN74LVC8T245s (midiori v2 verbatim), 74F138 glue, 5-way DIP. And **Nereori** (Nereid replica + midiori): 16 MB, **two** MIDI OUTs on **3.5 mm mini-jacks** with a jack→DIN-5 lead, USB host, LAN in preparation.

| Was | Now | Why |
|---|---|---|
| RAM base selectable 1 / 2 / 4 MB | **Per-bank enables, 2 MB steps**, requires ≥ 2 MB on board | XM-6BE10 does exactly this ("Target number memory is enabled when ON", DIP 1–5 → 2/4/6/8/10 MB) and tells you to fill the built-in memory first. Answers the open IPL-sizing question: contiguity is the constraint, and a 1 MB machine is not a target. |
| MIDI IN populated, OUT 2 as DNP | **OUT 2 populated, IN as DNP** | Both shipping cards are OUT-only; Nereori spends its second jack on a second OUT. |
| 3.5 mm TRS-A as our novelty | confirmed as the local norm | Nereori ships mini-jack MIDI with a converter lead. |
| X68030 support assumed | **not a target** without disabling its on-board memory (or an 060turbo) | XM-6BE10's own note. |
| timing margin a nice-to-have | **the differentiator** | Both cards warn midiori has "weak clock-up resistance"; use the standard clock (some units survive 1.2–1.5×). A timing-closed native bus slave is the clearest improvement our card can make. |
| 6 level shifters | consider **3** | The XM-6BE10 batch from 2025-10-18 halved them — pre-decode outside the FPGA. |

Buy-vs-build: a second-hand XM-6BE10 + midiori (¥26,000–70,500 at auction) is the fast path to a working machine. Ours stays worth building for TRS-A, PSRAM instead of ten SRAMs, overclock margin and an open design — not to save money.
