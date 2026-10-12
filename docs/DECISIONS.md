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

## 2026-10-10 — rev A netlist

`hw/netlist.py` is the single source of truth for what connects to what: it emits the KiCad netlist
for pcbnew, the Gowin `.cst` so the gateware cannot drift from the board, and a review report. Pin
names for standard parts are read out of the installed KiCad symbol libraries, so naming a pin a
part does not have is an error rather than a silent mistake, and every footprint is checked to exist.
66 components, 133 nets, all checks passing.

Changes made while writing it:

| Change | Why |
|---|---|
| **USB-C + FT232H deferred to rev B**; rev A has midiori's 2×5 1.27 mm JTAG header | ~20 nets of FT232H supply arrangement that could not be verified from here, plus a crystal and more rails, on a board that is already pin- and plane-constrained. A $5 FT232H breakout on the header does the same job for a run of 5. The 1.8 V JTAG requirement moves to the adapter. |
| `IDDIR` (slot B30) brought into the FPGA | the motherboard's own data-buffer direction signal; midiori gates its bus drive with it, and it is one spare channel on the control translator |
| **74AHCT1G34 buffers the data-bus DIR** to 5 V | `DIR`/`~OE` on an SN74LVC8T245 are referenced to `V_CCA`, which is the 5 V slot, so a 3.3 V FPGA output is below their V_IH of 0.7 × 5 V. midiori drives them directly and gets away with it; AHCT has TTL thresholds at 5 V and makes it correct. Address and control translators have `DIR` strapped to +5 V (A→B) and `~OE` to ground, so only the data pair needs the buffer — which also freed the `BUS_OE_N` pin. |
| MIDI IN sleeve grounded through a 0 R link (R11) | the MIDI spec leaves an IN socket's pin 2 open to stop ground loops, TRS gear usually grounds it; a link makes it a build option |
| 10 k pull-up on the H11L1 output | it is open collector |

Pin budget as built: **60 of 63 usable user I/O**. Banks 2 and 3 are full, three spare in bank 1.
Every pin except the clock (63) and JTAG is free to move within its own bank during routing — re-run
the script afterwards so the `.cst` follows the board.

Still open: the 3.3 V regulator's thermal check, whether MODE0/MODE1 can be reused as I/O after
configuration if the 1.8 V bank gets tighter, and the layout itself.

## 2026-10-10 — slot breakout

`hw/breakout.py`: the 2 × 50 fingers, six SN74LVC8T245, a 74AHCT1G34 for the data direction, two
74LVC2G07 for the open-drain returns, and two 2 × 20 IDC headers. 37 components, 117 nets, checks
passing. Reuses `netlist.py` wholesale, so the slot pin map and the part pin maps cannot diverge
between the two boards.

* **VREF from the host** on pin 1 of both headers sets the translators' B side, so the board matches
  whatever is plugged in. The Tang Console is 3.3 V and Sipeed warn 5 V on its headers destroys it.
  A local 3.3 V LDO (U10) and link (JP1) are fitted as DNP for a host that cannot source VREF.
* **Passive build**: leave the translators off and bridge the pairs, for a 5 V-tolerant RP2350 —
  option C from the original brief, now buildable on the same PCB.
* 28 signals per header, grounds every fifth pin plus the pair at the end. J2 carries everything a
  bus cycle needs, J3 the address bus and the bus-master spares, so a simple card ignores J3's tail.
* `~BGACK`, `~EXBERR` and `~EXNMI` get scope pads rather than header pins.

**Not connectable by PCIe.** The Tang Console's 16-pin FFC is an RPi-5-style PCIe lane on the
GW5AST's high-speed transceivers, not parallel I/O; the two 2 × 20 headers (~76 GPIO at 3.3 V) are
the only route for a 68000 bus.

**On RAM timing over the ribbon:** slow RAM costs wait states, not correctness, so DDR3 on the
Console would work — just slower than the production PSRAM. For proving the logic (IPL sizing,
`switch.x`, a RAM disk) use the 138K's ~765 KB of BSRAM for a 512 KB window with zero latency, and
leave the real timing number to the Nano 9K's PSRAM, which is the part that ships.

### 2026-10-10 — breakout placement

`hw/layout_breakout.py` builds `build/x68000-breakout.kicad_pcb` through KiCad's own `pcbnew`
Python API (10.0.4), so the file is valid by construction rather than hand-written S-expressions.
40 footprints, 117 nets, 76 × 150 mm, 4 layers, **DRC clean of errors** (48 silkscreen warnings
remain, which is normal before the reference text is nudged during routing).

* The finger-end geometry is copied from midiori's board — tab x 44–54, y 47–175 with 1 mm lead-in
  chamfers, steps out to x 58, body y 39–189. Only the depth is ours: 76 mm instead of 141 mm.
* Placement follows the signal flow: fingers, a column of six translators with their A side facing
  the fingers, then the two ribbon headers. Everything is on the top layer so the board assembles
  single-sided; the B-row fingers (the address bus) via up.
* Small parts are placed by first fit — 1 mm grid, nearest their anchor, first position that does
  not overlap. Crude, but the DRC run is the test.
* Two bugs the checks caught and a reader would not have: the first cap placement stacked parts on
  top of each other (36 shorting pads, 64 courtyard overlaps), and the IDC header footprint is
  natively long in Y, so rotating it 90° threw 50 mm of connector off the end of the card. Hand
  placements are now checked against the board rectangle exactly like the automatic ones.

Routing is the next step and is not done. The placement is a starting point, not a fixed plan;
anything may be moved.

### 2026-10-10 — breakout routing: planes yes, signals no (superseded, see below)

What landed: **three copper planes and 64 stitch vias, DRC clean** (0 errors). GND on In1.Cu, +5 V
and VREF sharing In2.Cu split left/right along the translator column — an 8T245 has its A-side
supply on the finger side and its B-side on the header side, so the split falls between them. That
takes the three largest nets out of the routing problem.

**Signal routing was attempted and abandoned, with numbers.** Drawing a direct segment between each
pair of pads gave **305 DRC errors**; moving the address translators to the bottom side so the two
big bundles never share a layer brought it to **266**. The geometry really is three monotonic fans,
which would be routable that way, but a segment drawn between two pads knows nothing about the pads
and tracks it crosses, and 48 of them cross a column of ICs. Doing it properly needs obstacle
avoidance and rip-up. `kicad-cli` has no Specctra exporter, so the route is: open in pcbnew,
File → Export → Specctra DSN, run freerouting, import the SES. Or route it by hand from here.

Changes the attempt forced, all of them improvements:

| Change | Why |
|---|---|
| **Address translators on the bottom side** | the connector's A row is on F.Cu and its B row on B.Cu, so data/control on top and address underneath gives each bundle its own layer. Costs double-sided assembly; worth it. |
| **Every duplicate supply pin is wired** | `symbol_pins` kept only the first pin of each name, so an 8T245's second V_CCB and its extra GNDs were left floating. Caught as DRC shorts when a plane stub ran into the orphan pad. Pin maps are now name → list. |
| **Stitch vias step outward until clear** | a fixed 0.9 mm stub lands on a neighbouring pad often enough to matter; the placer now tries 0.9 → 2.8 mm and reports the 30 pads where nothing fits. |
| Hand placements are checked like automatic ones | an IDC header rotated 90° put 50 mm of connector off the end of the card, and nothing complained. |

Open: 30 plane pads still have no room for a stitch via (mostly the translators' inner GND pins,
which the router will reach anyway once there is copper near them), and 172 unconnected items, which
is the unrouted signal count.

### 2026-10-10 — breakout routed with freerouting

**Every signal net is routed. 0 DRC errors, 1676 tracks, 200 vias.** The 15 remaining unconnected
items are all plane stubs (GND, +5 V, VREF) that need a via dropping to their pour — a few minutes
in pcbnew, and no signal is among them.

`hw/build_breakout.sh` runs it end to end and is the reproducible artifact: place, pour, export DSN,
freeroute, import session, fill, DRC, render. No GUI anywhere — `pcbnew`'s Python API has
`ExportSpecctraDSN` and `ImportSpecctraSES`, which is the piece `kicad-cli` lacks. Freerouting is the
local `tensorfleet/freerouting-otel` build; override with `FREEROUTING_JAR`.

Three findings worth keeping:

| Finding | Detail |
|---|---|
| **KiCad exports inner copper as *signal* layers** | so freerouting routed across In1 and In2 and cut both pours into islands. The symptom was 34 unconnected items of which **not one was a signal** — all plane fragments. Rewriting the DSN to mark In1.Cu and In2.Cu as `(type power)` keeps the pours whole and halved the count. |
| **The stock minimum track width blocked the fanout** | the board's 0.2 mm minimum rejected the 0.15 mm escapes an autorouter needs under a 0.65 mm pitch TSSOP: 79 `track_width` errors. JLCPCB's standard capability is 0.127 mm, so the minimum is now 0.15 mm, with netclass defaults left at 0.2/0.2/0.6. The stock minimum *clearance* was 0, which meant DRC was not checking it at all; now 0.15 mm. |
| **More stitch vias is not better** | letting the placer try eight directions instead of one outward axis placed ~20 more plane vias but crowded the channels enough to cost 2 DRC violations and leave 13 unconnected, against 0 and 15. Zero errors wins; the single-axis version stands. |

Still to do before fab: drop vias on those 15 plane stubs, tidy the silkscreen, and deal with the
edge-connector footprint's solder-mask bridges — the slivers between 2.54 mm gold fingers are below
KiCad's default minimum, which is correct for a normal footprint and wrong for an edge connector
(the finger area should be mask-free). Then gerbers.

### 2026-10-10 — plane stubs closed; the mask bridges were my own doing

**0 DRC errors at every severity. Every signal net routed. 12 unconnected items remain, all of them
plane stubs** (+5 V 6, GND 4, VREF 2) — a via each in pcbnew with the ratsnest showing.

`hw/build_breakout.sh` now runs the whole thing including `hw/fix_planes.py`, which drives itself
from DRC's own report: for each unconnected plane item it walks the track that item belongs to and
drops a via at the first point with room. A via on existing copper adds nothing that can collide
with a neighbour, which is why this is safe after routing when a stub track would not be.

**The solder-mask bridges were never a footprint problem.** They appeared only in the naive-routing
experiments — 202 of them, 138 attributed to J1 — because those tracks opened mask near the fingers.
A placement-only board had none, and the routed board has none at any severity. The edge-connector
footprint needs no change. I had put this down to the 2.54 mm finger pitch, which was wrong.

Four real faults the plane work exposed, none of which reading the files would have shown:

| Fault | Why it happened |
|---|---|
| **The VREF pour was 6 mm from every VREF pad** | the +5 V / VREF split has to fall *through* the translator column, not beside it. An 8T245 is 6 mm wide: V_CCA lands at x 67.1 and V_CCB at x 72.9, so a split at x 78 put both over the +5 V pour. Now at x 70.25. |
| **GND did not reach the edge fingers** | the pour started at x 56 and the fingers are at x 46–54. The pours are now single polygons shaped round the finger tab, kept clear of its edges. |
| **Flipping a footprint mirrors it in X** | so the bottom-side translators' supply pins landed on the wrong half of the split. Flip plus 180° rotation is a mirror in Y alone, which puts them back. |
| **Two abutting same-net rectangles are "intersecting", not merged** | hence one polygon per pour. |

Also added: GND poured into the leftover surface copper on both outer layers, which is standard and
does much of `fix_planes.py`'s job for free — a GND pad on F.Cu reaches the pour on its own layer
with no via at all.

Remaining before fab: 12 plane vias, silkscreen tidy (35 silk-over-copper, 15 overlaps), 3 dangling
vias to delete, then gerbers.

## 2026-10-11 — breakout finished to 0 DRC errors; fab package generated

`hw/tidy_board.py` closes the plane stubs that `fix_planes.py` gives up on (it also allows a short
stub track to reach a clear via spot, testing both the via and the stub against every other net) and
tidies the silkscreen: passives lose their reference text, which a machine-assembled board does not
need, and everything a person has to identify keeps its reference, moved clear of its own pads.
Silkscreen violations went 50 → 5.

**State: 0 DRC errors, every signal net routed, 8 unconnected items — all plane connections.** They
are the translator supply pins and two ground fingers, in the most congested corners; a stub there
would cross a routed track, so the script correctly declines. Each needs one via placed by hand.

Two things measured along the way: letting the stub placer use a 0.2 mm clearance produced a
`tracks_crossing` error, so it is 0.35 mm now — a stub that is never placed beats a stub that
crosses. And surface GND pours must stop at the finger tab: between gold fingers at 2.54 mm pitch a
pour is a row of slivers that fills as isolated islands and reports as unconnected zones.

`build/fab/` now holds gerbers, Excellon drill, the CPL and a BOM (`hw/make_bom.py`, generated from
the netlist since there is no schematic to run `sch export bom` against). **The LCSC column is
deliberately empty** — assembly matches by LCSC number against live stock and a guessed number buys
the wrong part.

[FAB-BREAKOUT.md](FAB-BREAKOUT.md) is the ordering guide and, more usefully, the list of six things
no person has checked: there is no schematic so ERC has never run; the edge-finger geometry is
copied from midiori and has never been in a real slot; hard gold and the 45° bevel are order notes
that are not in the gerbers; the footprints have not been checked against buyable parts; JLCPCB's
rotation convention differs from KiCad's and six parts are on the bottom; and the LCSC numbers are
missing. The board is not ready to order until those are addressed.

### 2026-10-11 — the last plane connections: stop hand-placing vias, give the router anchors

8 unconnected → **3, still 0 DRC errors**. All three are GND: one ground finger and two
surface-pour islands.

Four approaches, measured, in order:

| Approach | Result |
|---|---|
| per-pad stub and via, after routing | gave up on 30 pads; the board is full by then |
| same, searching eight directions | 2 DRC violations, 13 unconnected |
| same, run **before** routing so there is room | **worse**: 12 unconnected — the vias got in the router's way |
| **sparse via anchors per pour, let freerouting reach them** | 7, then 5, then **3** |

The lesson is the one the whole routing exercise keeps teaching: freerouting is better at finding a
path than any heuristic in this repo, so the job is to give it targets rather than to do its work.
A coarse grid of vias per pour, plus one anchor just off the end of each translator's supply pins
(0.65 mm pitch with routing on three sides is the hardest pad on the board), and it connects them.

Two refinements that each cost a rebuild to find:

* **Delete the anchors the router did not use** — 82 of them, holes nobody should pay to drill.
* **Except the GND ones.** They are not spare targets, they are the stitching that ties the GND
  pours on F.Cu, In1 and B.Cu into one piece; deleting them split the ground into islands that DRC
  reported as unconnected zones. That alone took it from 5 to 3.

Left: 3 GND connections, 5 silkscreen labels on copper, and 81 `via_dangling` warnings that are
almost certainly KiCad flagging via-to-pour connections — connectivity says the plane nets are
complete — but want a human to open a few and confirm before 337 holes get drilled.

## 2026-10-12 — a silent pipeline failure, and the overview page

**The important finding is a bug in my own tooling, not in the board.**
`hw/build_breakout.sh` was running freerouting from a background shell without
redirecting stdin. Freerouting read stdin, got EOF, and exited before routing
anything — and because its output went through a `… || true` pipe, the run
reported success and left an *unrouted* board on disk. Two rebuilds in a row
produced a board with 191 vias and zero tracks while the stale `drc-all.json`
still described the previous, good board. I came close to reporting that state
as finished.

Fixed three ways, because one is not enough for a failure that looks like
success: `java … < /dev/null`; `test -s …ses` after it; and an assertion in the
import step that the board has more than 500 tracks. Also `fix_planes.py` now
refuses to add a second pair of surface pours when re-run, which had been
stacking zones on each other and producing `zones_intersect` errors.

Two smaller faults found on the way:

* `segments_cross` — the obstacle test for a new stub used the minimum distance
  between the endpoints of two segments, which is exactly wrong for an X
  crossing: all four endpoints are far apart. That put seven `tracks_crossing`
  errors on a board. Replaced with a real orientation test, in both
  `tidy_board.py` and `fix_last_gnd.py`.
* An 8 × 1.8 mm gold finger modelled as a 4 mm-radius circle blocked every route
  east of the edge connector, which is why no ground finger could be given a
  via. Pads are rectangles now.

State after a clean, verified run: **0 DRC errors, 5 unconnected, all GND** —
one ground finger and four pour-island pairs, two underlying problems. Every
signal net routed. Note that freerouting's result varies run to run; the count
moves by one or two between rebuilds.

`hw/make_overview.py` writes `docs/boards.html`: 3D renders of both sides, a
tabbed view of all four copper layers, the live DRC and track counts read from
the board itself, the MIDI card's status, and the cover plate drawn from
`mech/plate.py`'s own constants. Self-contained, as the enclosure guides are.
`mech/plate.py` now imports cadquery inside its functions so those constants can
be read without pulling in OCP.

## 2026-10-12 — right-angle ribbon headers, and the two-board question

`IDC-Header_2x20_P2.54mm_Horizontal` instead of the vertical part. The pads are
identical — two columns 2.54 mm apart over 48.26 mm — so the netlist and the routing are untouched;
only the body changes, from 11 mm wide to 16.2 mm, extending +13.8 mm from pad 1. At rotation 0 that
puts the cable exit toward the rear plate.

Right on its own merits: a vertical header sends the ribbon straight up out of the card and into the
neighbouring slot, which is wrong in a machine whose cards are 20 mm apart. But the reason it came up
is better than that.

**There is no companion board, and that is the current plan rather than a gap.** The breakout's
ribbons are meant to reach a dev board already on the bench — Tang Console 138K for logic, Tang Nano
9K for PSRAM timing. Memory and MIDI as a *product* is the full-size card in `hw/netlist.py`, which
carries the FPGA, its in-package 8 MB PSRAM and the MIDI drivers by itself and does not use the
breakout at all.

With the cable now leaving in the plane of the card, a second option exists that did not before: a
two-board set in one slot, the breakout at the fingers and a logic card behind it, joined by a short
ribbon. Its merits, honestly weighed:

| For | Against |
|---|---|
| Revising the logic half costs one small board, not a 150 × 76 mm one | Two PCBs, two assembly setups, a ribbon that can come loose |
| The breakout is then the same PCB on the bench and in the machine | A 40-way ribbon carrying a 68000 bus is worse electrically than 20 mm of trace |
| The bus half is the part that is hardest to get right and cheapest to freeze | The single card already has a complete netlist |

Not committed to. The single-card design stands; the headers simply no longer rule the other out.

Rebuild after the change: 0 DRC errors, 6 unconnected (all GND, same two underlying problems).

### 2026-10-12 — 76 mm to 69 mm, so the next board can sit against it

`RIGHT` drops from 120 to 113. The right-angle shrouds now end 3.2 mm from the rear edge instead of
8 mm, so whatever plugs in — a ribbon to a Tang board on the bench, or a companion logic card in the
machine — sits immediately alongside rather than across a stub of empty board. 7 mm less PCB per
card as a bonus.

The geometry works inside the case: the slot takes midiori's 141 mm, so a 69 mm breakout at the
finger end leaves ~70 mm of depth behind it for a logic card, with the plate at the far end. Both
boards stay inside the machine.

One source of truth now: `RIGHT` lives in `layout_breakout.py` and `route_breakout.py` and
`fix_planes.py` import it, because the pour outlines and anchor regions had the old 118/119 hard-coded
in three places and would have silently poured copper past the board edge.

Result: **0 DRC errors, 4 unconnected** (all GND) — one better than before the change, which is
freerouting's run-to-run variation rather than anything the narrowing did.

## 2026-10-12 — the logic board, and what the split costs

`hw/logic.py` generates the other half: the FPGA, its in-package 8 MB PSRAM, the MIDI drivers and
jacks, three rails, the JTAG header, the DIP switches and the oscillator. **47 parts, 87 nets, all
checks passing, 60 of 63 usable FPGA pins.** No layout yet.

Taken together the two boards *are* the single-card design, cut along the translators. The breakout
keeps the fingers, the six translators and the plane split; the logic board takes everything after
them. What the split buys is that a respin is a 47-part board instead of a 150 mm one, and the bus
half — the part that is hardest to get right and cheapest to freeze — stops moving. What it costs is
a 40-way ribbon carrying a 68000 bus and one more assembly.

**The split forced a change to the breakout, and this is the useful finding.** The logic board's pin
budget only closes if A1–A15 arrive at **1.8 V**: bank 3 is the only place fifteen more signals fit,
and bank 3 must be 1.8 V because it carries the PSRAM. So the breakout now carries two reference
voltages — VREF on header pin 1 for data, control and A16–A23; VREF_LOW on pin 3 for A1–A15 — and
its address translators regroup accordingly: U3 takes A1–A8, U4 takes A9–A15 (seven lines, one
spare), U5 takes A16–A23.

Consequences worth stating plainly:

* **CLK10M is gone** from the breakout. It was the eighth channel on U5 and the regrouping needed
  that slot. No loss: the card runs from its own 27 MHz oscillator and the bus is asynchronous.
* **A 3.3 V-only host gets 48 signals, not 63.** A Tang Console fits JP2, ties the two references
  together and can then exercise a MIDI card's worth of bus but not the 8 MB window, because the
  bank-3 pins are unusable at 3.3 V. Worth knowing before planning bench bring-up around it.
* **VREF_LOW is routed, not poured.** It feeds two translators' B sides — tens of milliamps — so a
  third split in the In2 plane would have bought nothing and complicated the two that matter.

Breakout after the change: 0 DRC errors, 4 unconnected (all GND, the same two problems).

## 2026-10-13 — laying out the logic board, and what a Tang Console actually needs

`hw/layout_logic.py`, `hw/route_logic.py`, `hw/build_logic.sh` — same pipeline as the breakout's,
deliberately not shared code: every constant that matters differs, and one parameterised placer for
two boards would read worse than two placers. 70 × 150 mm, ribbons on the left edge facing the
breakout (headers rotated 180 so the shrouds point back at it), FPGA in the middle, MIDI jacks on
the right at the slot plate.

**The pad check caught a footprint that would have made the board unbuildable.** `TRS` was mapped to
`Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles`, which is a *mono* TS jack with a tip switch —
pads S, T and TN, no ring at all. A MIDI current loop needs tip, ring and sleeve. Now
`Jack_3.5mm_CUI_SJ1-3533NG_Horizontal_CircularHoles`, a real stereo jack, and horizontal so the
socket looks out of the board edge through the plate. The breakout has no jacks, so this never
surfaced there.

**Anchor density matters more than I thought.** At the breakout's 8 mm pitch, a 70 × 150 board takes
215 plane vias — one every 48 mm² — and freerouting ran for over twenty minutes of CPU without
producing a session. At 14 mm it is 85 and the run completes. The anchors are meant to give the
router targets, not to fill the board with obstacles.

### What supporting a Tang Console needs — and a correction

I said earlier that a Console "has 48 signals instead of 63". **That was wrong.** The 48-pin limit is
the *logic board's* FPGA banks — bank 3 must be 1.8 V because it carries the PSRAM, so only 48 of
its pins are 3.3 V. A Tang Console has ~76 GPIO, all 3.3 V, and with the two references tied it can
take every signal the breakout carries. The limitation never applied to it.

What it does need:

| | |
|---|---|
| **JP2 fitted** | ties VREF_LOW to VREF so all 55 signals arrive at 3.3 V. Already a DNP part on the board. |
| **JP3 left out** | header pin 2 carries the slot's 5 V. Sipeed warn that 5 V on a Console header destroys it, so that link is now DNP and the pin is dead unless you ask for it. Added today; a live 5 V pin on a connector meant for 3.3 V boards was a trap. |
| **An adapter, not a straight ribbon** | our 2 × 20 pinout is ours — VREF, +5 V, VREF_LOW, then signals with a ground every fifth pin. Sipeed's is Sipeed's. A straight ribbon would land our grounds on their I/O. Hand-wired ribbons for bring-up, or a small adapter PCB if it becomes routine. |
| **Console banks at 3.3 V** | Sipeed's own documentation says they are, but it is worth confirming before plugging anything in. |

The honest summary: the Console is a bring-up tool, and a hand-made pair of ribbons is the right
amount of effort for it. Designing the breakout's pinout around one dev board would spoil it as a
general instrument.

### 2026-10-13 — I fenced the FPGA in, then blamed the layer count

The logic board would not route, and the reason was mine. `route_logic.py` gave each of the FPGA's
eighteen plane pads its own via anchor 7-10 mm from the package centre. The package's pads end at
5 mm. That put **seventeen vias in the annulus between 5 and 12 mm** — exactly the ring every one of
the sixty signal escapes has to cross — and then asked freerouting to get out through it.

What that cost, in order:

| Attempt | Result |
|---|---|
| 4 layers (In1 GND, In2 power), anchors ringing the FPGA | 85 of 165 unrouted, 46 violations, 21 min |
| **6 layers** (In1 GND, In4 power, In2/In3 free), same anchors | **92 unrouted, 52 violations, 51 min — worse** |

The six-layer run is the useful data point: adding two signal layers made it *worse*, which is the
tell that the obstruction was local to the package and not a shortage of routing space. I had already
written the four-layer failure up as "sixty signals cannot fan out on two signal layers", which was a
plausible story and the wrong one.

The fix: no anchors within 12 mm of the FPGA at all. Ground gets a 5 × 5 grid of thermal vias inside
the exposed pad, which is what a QFN wants anyway; the supply pins reach the pours through the same
coarse grid as everything else. Also dropped the default netclass to 0.15 mm track and clearance —
freerouting routes to the netclass, not to the board minimum, and 0.2/0.2 leaves nothing around a
0.4 mm pitch part.

Six layers stays for now because it is not the variable under test, but it is probably unnecessary:
worth re-running at four once the board routes, since it halves the fab cost.

**The lesson for the next board**: an anchor is an obstacle. Put them where the router wants targets,
never where it wants room.

### 2026-10-13 — removing the fence worked, and exposed the next thing

With no anchors inside 12 mm of the FPGA: **92 unrouted → 23**, score 480 → 840, 13 minutes instead
of 51. The fence really was the whole of the first problem.

What that uncovered, in the 222 DRC violations left behind:

| Finding | Detail |
|---|---|
| **The stock QFN-88 land pattern is not manufacturable at standard capability** | KiCad's `ArtInChip_QFN-88-1EP_10x10mm_P0.4mm` has 0.30 mm pads on a 0.40 mm pitch — a **0.10 mm** copper gap. That is below JLCPCB's standard 0.127 mm, and it made every adjacent pad pair of different nets a clearance error (84 of them) before a track was drawn. `hw/make_qfn_footprint.py` derives `x68k:QFN-88-...-_Standard` with 0.22 mm pads and a 0.18 mm gap, leaving positions and the exposed pad alone. |
| **29 tracks came out at 0.112 mm** | freerouting squeezing through that same escape, below both the board minimum and standard capability. Expected to go away now the pads are narrower; if it does not, the escape needs a finer process or a different package. |

**The 0.22 mm width needs checking against Gowin's package drawing before fab.** It is a normal IPC
land width for a 0.4 mm pitch QFN, but normal is not verified, and this is the expensive part on the
board.

Worth revisiting once it routes: six layers was adopted on a wrong diagnosis and has never been
tested against a clean escape. Four may well do, and halves the fab cost.
