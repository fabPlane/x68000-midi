# Hardware specification — rev A

Everything a schematic needs that is not a judgement call at the keyboard. Pin
data is extracted from Gowin UG803 into [`hw/gw1nr9_qn88p_pins.csv`](../hw/gw1nr9_qn88p_pins.csv)
(88 pins, 70 user I/O, bank per pin) rather than typed from a reading of it.

Decisions and their reasons are in [DECISIONS.md](DECISIONS.md); gateware in
[../gateware](../gateware).

## 1. The constraint that shapes the board

The GW1NR-9 in QN88P carries its 64 Mbit PSRAM on **bank 3**, and UG803 requires
`VCCIO3 = 1.71–1.89 V` because that bank both powers and talks to the die. So the
FPGA has two I/O voltages and the bus has to be split across them:

| Bank | Pins | Voltage | Count | Notes |
|---|---|---|---|---|
| 1 | 48–57, 59–63, 68–77 | 3.3 V | 25 | also the SPI config pins (53–62) and `RPLL_T_in` (63) |
| 2 | 17–20, 25–42, 47 | 3.3 V | 23 | `GCLKT_4`/`GCLKC_4` on 35/36 |
| 3 | 3, 5–11, 13–16, 79–88 | **1.8 V** | 22 | PSRAM interface; **JTAG (5,6,7,8), RECONFIG_N (9), MODE0/1 (87,88) live here** |
| 0 | — | 3.3 V (VCCX) | 0 | supply only on this package |

Consequences, none of them optional:

* **The USB-JTAG bridge must drive 1.8 V.** TMS/TCK/TDI/TDO are bank 3 pins. An
  FT232H with `VCCIO = 1.8 V` is fine; a 3.3 V-only programmer is not.
* **Usable 1.8 V I/O is 15**, or 18 if MODE0/MODE1 are strapped with resistors
  and reused after configuration and RECONFIG_N is left to the JTAG. Plan for 15
  and treat the other three as margin.
* **Usable 3.3 V I/O is 48.**

## 2. Pin budget

| Group | Pins | Rail | Notes |
|---|---:|---|---|
| D0–D15 | 16 | 3.3 V | 16-bit: a 68000 word access asserts `~UDS`+`~LDS` and expects 16 bits, so a byte-wide port is not an option for the RAM window |
| A16–A23 | 8 | 3.3 V | one LVC8T245 at 3.3 V |
| A1–A15 | 15 | **1.8 V** | two LVC8T245 with `VCC_B = 1.8 V`; 16th line spare |
| `~AS` `~UDS` `~LDS` `R/~W` `~IACK2` `~IACK4` `~EXRESET` | 7 | 3.3 V | inputs only |
| `~DTACK` `~IRQ2` `~IRQ4` | 3 | 3.3 V | out to open-drain buffers |
| translator `DIR`, `~OE` | 2 | 3.3 V | shared across the data transceivers |
| MIDI TX1, TX2, RX | 3 | 3.3 V | RX populated, its jack DNP |
| RAM bank enables (DIP) | 4 | 3.3 V | 4 × 2 MB |
| 27 MHz clock in | 1 | 3.3 V | **pin 63, `IOR5A/RPLL_T_in`** — the PLL's own input |
| status LED | 1 | 3.3 V | bring-up sanity, worth the pin |
| **3.3 V total** | **45** | | of 48 — 3 spare |
| **1.8 V total** | **15** | | of 15 (18 with the MODE/RECONFIG pins) |

JTAG's four pins are outside both totals; they are fixed in bank 3.

The address split is the only awkward part, and it is forced: 22 address lines
plus a 16-bit data bus do not fit in 48 pins of 3.3 V I/O, and bank 3 is the only
other place to put them.

## 3. Block diagram

Every transceiver has its A side on the slot at `VCCA = 5 V` and its B side at
the voltage of the FPGA bank it feeds. The SN74LVC8T245 is specified from 1.65 V
to 5.5 V on each side independently, which is the whole reason this split works.

```
slot 2x50 (5 V) ── 2 x SN74LVC8T245  D0-D15     VCCA 5 V  VCCB 3.3 V ── FPGA banks 1/2
                ── 1 x SN74LVC8T245  A16-A23    VCCA 5 V  VCCB 3.3 V ── FPGA banks 1/2
                ── 2 x SN74LVC8T245  A1-A15     VCCA 5 V  VCCB 1.8 V ── FPGA bank 3
                ── 1 x SN74LVC8T245  controls   VCCA 5 V  VCCB 3.3 V ── FPGA banks 1/2
                ── 2 x 74LVC2G07     ~DTACK ~IRQ2 ~IRQ4 (open drain)   ── FPGA
                   +5 V ── 3.3 V 1 A ── 1.8 V ── 1.2 V
FPGA ── 74LVC1G07 + 220 R x2 ── TRS-A  OUT      (5 V current loop)
     ── 74LVC1G07 + 220 R x2 ── TRS-A  OUT 2
     ── H11L1 + 220 R + 1N4148 ── TRS-A IN      (jack DNP)
     ── USB-C + FT232H (1.8 V IO) ── JTAG + debug UART
     ── 64 Mbit PSRAM, in package
```

## 4. Rails

| Rail | From | Load | Part |
|---|---|---|---|
| +5 V | slot A49/A50, B49/B50 | translator A-side, MIDI OUT current loop | — |
| 3.3 V, 1 A | +5 V | VCCX, VCCIO1/2, translator B-side, opto, oscillator, FT232H core | AP2114 or similar 1 A LDO (1.7 V drop × ~0.4 A = 0.7 W — check the package, or use a buck) |
| 1.8 V | 3.3 V | **VCCIO3 + PSRAM**, JTAG side of the FT232H | 300 mA LDO |
| 1.2 V | 3.3 V | FPGA core | 300 mA LDO (midiori uses AP2127K-1.2) |

Sequencing: Gowin asks for VCC (1.2 V) and VCCX to be sane at power-up; midiori
brings 3.3 V up first and lets the 1.2 V LDO follow, which is the usual
arrangement and is what rev A should copy.

±12 V is present on the slot (A22/A23, A32/A33) and unused. midiori v3.0 uses it
for an audio DAC — the PCM mix-in's supply is already there if v2 wants it.

## 5. Bill of materials

| Ref | Part | Qty | Source | ≈ USD |
|---|---|---:|---|---:|
| U1 | GW1NR-LV9QN88PC6/I5 | 1 | JLCPCB C5799578 | 23.50 |
| U2–U7 | SN74LVC8T245 (TSSOP-24) | 6 | JLC extended | 3.00 |
| U8–U9 | 74LVC2G07 / 1G07 open drain | 4 | JLC basic | 0.80 |
| U10 | H11L1 optocoupler | 1 | MIDI IN | 0.70 |
| U11 | FT232H (or FT2232H) + USB-C | 1 | JTAG + UART, **VCCIO 1.8 V** | 4.00 |
| U12–U14 | 3.3 V 1 A, 1.8 V, 1.2 V LDOs | 3 | | 1.00 |
| X1 | 27 MHz oscillator, 3225 | 1 | /3456 = 31250 exactly | 0.50 |
| J1 | X68000 2×50 edge, on the PCB | — | gold fingers | — |
| J2–J4 | 3.5 mm TRS, PCB mount, panel nut | 3 | OUT, OUT 2, IN(DNP) | 1.00 |
| SW1 | DIP-4 | 1 | RAM bank enables | 0.30 |
| D1 | 1N4148 | 1 | across the opto LED | 0.05 |
| — | 0402/0603 passives, 220 Ω ×4, decoupling | ~60 | one 100 nF per supply pin | 2.00 |
| PCB | 150 × 141 mm, 4 layer, ENIG + **hard gold bevelled fingers** | 1 | run of 5 | 8–12 |
| Plate | 176 × 20 × 2 mm, 3 jack holes | 1 | from midiori's TRS plate | 1.00 |
| | **per card, run of 5, before assembly** | | | **≈ 50** |

## 6. Fab notes

* 4 layer, 1.6 mm. Stackup signal / GND / power / signal; the 1.8 V and 3.3 V
  pours share the inner plane, split under the FPGA with bank 3 over its own
  island.
* **Edge fingers: hard gold, 30 µ", and bevelled 45°.** midiori's ERRATA records
  that its fab would not bevel the fishtail edge — specify it explicitly and check
  the quote.
* The finger footprint, the guide notches and the M3 plate holes at 136 mm centres
  come from `hw/lib/x68k.pretty/x68000_expansion.kicad_mod` and midiori's outline;
  150 × 141 mm is kept for rev A so the card-guide fit is known good.
* JLCPCB SMT on the top side only; the TRS jacks, DIP and USB-C are hand-soldered
  after, unless JLC stocks them.
* Keep the 220 Ω MIDI resistors and the opto away from the switching edge of the
  USB bridge; the current loop is slow but the opto is not noise-immune.

## 7. What is not settled here

* **Schematic capture and PCB layout.** This document is the input to them, not a
  substitute. The layout — QFN-88 escape, six transceivers, a 100-pin edge
  connector, a split inner plane — is skilled KiCad work and has not been started.
* The exact pin-by-pin assignment within each bank. The budget above proves it
  fits and fixes the two pins that are not free (clock on 63, JTAG in bank 3);
  the rest should be assigned during layout for routability and then frozen into
  the gateware constraint file.
* 3.3 V regulator choice: an LDO dissipating ~0.7 W needs the thermal check, and a
  buck may be the right answer.
* Whether MODE0/MODE1 can be reused as I/O after configuration on this part — only
  needed if the 1.8 V budget gets tighter.
* PSRAM pin constraints for the open toolchain: apicula documents the SDRAM bond
  pads for LQ144P but not the QN88P PSRAM, so one vendor-tool run is needed to
  read the placement (see DECISIONS 2026-09-20).
