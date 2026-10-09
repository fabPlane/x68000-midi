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

## 2026-10-09 — bus timing is the design goal

Read of `reference/midiori/gateware/midiori.py` (the `#register io state machine` FSM) against the
68000 slot cycle. midiori's weakness is architectural, not Lattice-vs-Gowin:

- The whole design runs at **16 MHz** (`UART(..., 16000000, 31250)`, `default_clk_period = 1e9/16e6`).
  The bus FSM therefore sees the slot with **62.5 ns granularity**.
- `~AS`, `~LDS`, `R/~W`, `~IACK` and A1–A23 go **straight into the FSM with no synchronisers**.
- `IDLE → RDATA/WDATA` needs `~AS` still low at an internal clock edge. A 25 MHz 68030 cycle holds
  `~AS` low for ~80 ns ≈ 1.3 internal clocks, so a cycle can be **missed entirely**; a write can reach
  `WDATA` after the data has gone.
- In `RDATA`, `_dtready` and the read-data mux assert **in the same state**, so the data crosses a wide
  combinational `register_num` tree plus a translator while the CPU is already counting down to its
  latch edge (one bus clock: 100 ns at 10 MHz, **40 ns at 25 MHz**).
- `RDATA` exits only when `~AS == 1` is sampled on a 16 MHz edge, so the card can keep driving the data
  bus up to 62.5 ns into the **next** cycle.

Rules for our bus slave, in priority order:

1. **Fast sample domain.** PLL the 27 MHz oscillator to **108 MHz** (4×) for the bus front end — 9.26 ns
   granularity, ~7× midiori's. The YM3802 and UART stay on a slow clock (27 MHz / 864 = 31 250 exactly).
2. **Honest `~DTACK`.** Assert it only after the read data is registered in the IOB and the output enable
   is on — never in the same cycle as the mux. Late DTACK costs a wait state; early DTACK corrupts.
3. **Registered read data.** Decode the register value continuously from the (long-stable) address and
   register it; at access time the path is FF → IOB → translator only.
4. **Asynchronous release.** Gate the data-bus OE combinationally off `~AS` high, not off a clocked state
   exit, so the card is off the bus at any CPU speed.
5. **2-FF synchronisers** on every asynchronous control input; at 108 MHz they cost 18.5 ns.
6. The RAM path is allowed to be slow — PSRAM random read plus refresh collisions will need wait states,
   and with honest DTACK that is correct rather than broken. XM-6BE10's async SRAM will beat us on raw
   RAM latency; we trade that for one chip and for working when the machine is clocked up.

**Measurement, not hope:** program the Tang Console 138K as a parameterised **68000 bus master** —
generate read/write/IACK cycles with 10 / 16 / 25 / 33 MHz timing, sweep the DTACK-and-data window, and
find where the card actually fails. That characterises overclock margin on the bench without owning an
overclocked X68000, and it is the same rig the slot breakout needs.

### 2026-10-10 — first silicon-free numbers

`gateware/x68kmidi/bus.py` (slave) and `gateware/x68kmidi/busmaster.py` (instrument) are written and
tested; 22 pysim tests pass. The sweep (`tools/sweep.py`, table in `gateware/README.md`) gives a
**constant 6-clock DTACK latency — 55.6 ns at 108 MHz, 44.4 ns at 135 MHz — independent of CPU
speed**, correct reads, writes and IACK vectors from 6.8 MHz to 54 MHz emulated, no bus contention,
and wait states appearing (1 at 27 MHz, 2 at 54 MHz) instead of failures. That is the whole design
goal: being slow is allowed, being early or absent is not.

Honest limits: simulation has no translator, IOB or PCB delay, so add ~10–15 ns before comparing
with a real DTACK window — which means we do *not* expect zero wait states on a 25 MHz X68030, only
correctness. And midiori has not been run on the same rig; porting its FSM as a reference model is
the way to make the comparison a measurement rather than an argument.

Tuning knob if the latency ever matters: 2 of the 6 clocks are the input synchronisers. Driving the
data bus speculatively off the raw `~AS` and using the synchronised copy only for `~DTACK` would cut
it to ~3 clocks, at the cost of a deliberate metastability argument. Not done; not needed yet.

### 2026-10-10 — the reference model, and a correction

`gateware/x68kmidi/midiori_ref.py` ports midiori's bus FSM faithfully (own 16 MHz domain, no
synchronisers, DTACK asserted in the same state as the read-data mux, release only on a clock edge)
so the comparison is a measurement. `tools/compare.py` runs both on the same rig at every phase of
the two clocks; the table is in `gateware/README.md`.

**The result does not support the strong form of the 2026-10-09 claim.** In simulation the midiori
architecture is correct at every phase up to 18 MHz emulated, ties our zero-wait-state ceiling at
18 MHz, and only degrades at 27 MHz (25 of 27 phases) and 54 MHz (none). Adding 27.8 ns of
data-path delay does not break it either. The reason is the 68000 itself: a late `~DTACK` only buys
wait states, and the cycle leaves a full bus clock between sampling `~DTACK` and latching the data,
which absorbs a slow mux. So "midiori misses cycles on a fast machine" is wrong as stated — our
rules 1-4 buy margin the bus largely provides anyway.

What survives, and is now the real justification:

- **Rule 5 (synchronisers) is the one that matters.** midiori feeds unsynchronised `~AS`, `~LDS` and
  23 address lines into its FSM. Simulation resolves that cleanly every time; hardware does not, and
  the failure it produces — rare, random, worse as edge density rises — is exactly the shape of the
  reported "weak clock-up resistance". It is also the one thing the rig cannot score.
- **Rule 1 (108 MHz) is what makes rule 5 affordable**: two synchroniser stages cost 18.5 ns there
  and 125 ns at midiori's 16 MHz, which would be a real penalty.
- Rules 2-4 remain cheap correctness insurance, and show up at the extremes (54 MHz emulated) rather
  than on any real machine.

Practical consequence: do not sell this card on "better timing" without hardware evidence. The
bench claim we can make is narrower and still worth making — synchronised inputs, no asynchronous
sampling anywhere, and a release path that cannot collide with the next cycle.
