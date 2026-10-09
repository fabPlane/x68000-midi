# Gateware

Target: Gowin GW1NR-LV9QN88PC6/I5. Prototyping boards: **Tang Console 138K** (GW5AST-LV138PG484A, 1 GB DDR3, 2 × 40-pin + 2 × PMOD, BL616 USB-JTAG — on hand) for the logic, **Tang Nano 9K** (the production chip) for the PSRAM.

```
make venv                 # uv sync -> .venv (Python 3.14, Amaranth 0.5.10 — pins mirror vodoo_2/amaranth)
make test                 # pysim tests
make rtl-module MOD=UartTx PARAMS="clk_freq=27000000"   # -> rtl/UartTx.v
```

| What | Console 138K | Nano 9K |
|---|---|---|
| YM3802 transmit core, UART, IRQ/vector logic | yes | yes |
| MIDI OUT into the MIDI BOX (PMOD → 3.3 V driver 33 Ω/10 Ω → TRS-A) | yes | yes |
| 68000 bus slave against a real X68000 (needs a slot breakout with level shifters → 40-pin) | yes | yes |
| 8 MB RAM window behaviour, IPL sizing (DDR3 or BSRAM stand-in) | logic yes, timing no | — |
| **PSRAM read latency, bank-3 1.8 V I/O** — the numbers rev A depends on | no (no PSRAM) | **yes** |
| Toolchain | Gowin EDA (GW5A open flow is in progress in vodoo_2, not hardware-valid yet) | Amaranth + apicula, Gowin EDA fallback |

## Modules

| Module | Status | From |
|---|---|---|
| `x68kmidi.uart.UartTx` | ported, tested (8N1 timing at 27 MHz) | midiori `UART` |
| `x68kmidi.bus.BusSlave68k` | written, tested | midiori's register FSM, re-architected to the six rules |
| `x68kmidi.busmaster.BusMaster68k` | written, tested | new — the measuring instrument |
| `x68kmidi.midiori_ref.MidioriBusSlave` | written, tested | faithful port of midiori's bus FSM, for comparison |
| `x68kmidi.sim_harness` | — | master + slave + register file, slave swappable |
| `x68kmidi.ym3802.Ym3802` | written, tested | midiori `Midiori` register file + transmitter |
| `x68kmidi.card.Card` | written, tested end to end | new — two ports, `$EAFA00`/`~IRQ2` and `$EAFA10`/`~IRQ4` |
| YM3802 transmit FIFO + ITX (active-sensing 0xFE) wrapper | next | midiori `Midiori` |
| PSRAM window (third decoder on the same bus) | next | zf3/psram-tang-nano-9k as an `Instance` |
| YM3802 receive side | last | new |
| PSRAM window | later | zf3/psram-tang-nano-9k (Verilog `Instance`) |
| YM3802 receive side | last | new |

## Plan

1. Port `reference/midiori/gateware/midiori.py` (Migen) to Amaranth, module by module, each with a pysim test — no board needed.
2. Transmit core + a small in-FPGA 68000-bus stimulus; loop 31 250 bps into the MIDI BOX. Console first (Gowin EDA), then Nano 9K (apicula).
3. **Slot breakout PCB**: 2 × 50 fingers + 6 translators + 2 × 20 header, no FPGA. Proves the finger footprint, bevel, plate and card guides cheaply, and connects either dev board to a real X68000 for the bus-slave bring-up (~DTACK timing, IRQ vector on ~IACK, IPL sizing with a BSRAM/DDR3 stand-in window).
4. Nano 9K: wrap `zf3/psram-tang-nano-9k` as an `Instance`; measure random 16-bit read latency; find the PSRAM pad placement for the open flow.
5. YM3802 receive side — last, only when someone needs STed2 recording.

## Clocking

One 108 MHz domain for the whole card, PLL'd x4 from the 27 MHz oscillator:
108 MHz / 31250 = 3456 and 108 MHz x 4 us = 432, both exact, so the MIDI bit rate and the YM3802
timer tick need no second clock, no fractional divider and no clock-domain crossing anywhere
between the slot and the jack. (The PSRAM controller will bring its own domain.)


## Speed sweep

`uv run --project gateware python -m tools.sweep [master_mhz]` drives the slave with
`BusMaster68k` at a range of emulated CPU clocks and reports what the slave did. With the bus
front end at 108 MHz:

```
 emulated CPU  S len        DTACK latency  wait states  result
       6.8 MHz      8      6 clk =  55.6 ns            0  ok
      10.8 MHz      5      6 clk =  55.6 ns            0  ok
      13.5 MHz      4      6 clk =  55.6 ns            0  ok
      18.0 MHz      3      6 clk =  55.6 ns            0  ok
      27.0 MHz      2      6 clk =  55.6 ns            1  ok
      54.0 MHz      1      6 clk =  55.6 ns            2  ok
```

The point is the constant latency column: the slave's answer does not depend on how fast the CPU
asks, so going faster buys wait states rather than failures. At 135 MHz the same six clocks are
44.4 ns. Simulation contains no translator, IOB or PCB delay — add roughly 10–15 ns before
comparing with a CPU's DTACK window.

## Head to head

`uv run --project gateware python -m tools.compare [master_mhz] [dtack_delay_clk] [data_delay_clk]`
runs ours and the midiori reference model (its own 16 MHz domain, no synchronisers, DTACK with the
data, clocked release) on the same rig, at every phase of the two clocks:

```
 emulated CPU |   ours: ok  wait  clash |   midiori: ok      wait  clash
------------------------------------------------------------------------
       6.8 MHz |      1/1     0      0 |        27/27     1      0
      10.8 MHz |      1/1     1      0 |        27/27     1      0
      13.5 MHz |      1/1     1      0 |        27/27     1      0
      18.0 MHz |      1/1     1      0 |        27/27     1      0
      27.0 MHz |      1/1     1      0 |        25/27     1      0
      54.0 MHz |      1/1     2      0 |         0/27     3      0

fastest zero-wait-state read: ours 18.0 MHz, midiori model 18.0 MHz
```

Read this honestly: the reference model is **fine at every speed a real X68000 runs**, and the
zero-wait ceiling is a tie. Adding up to 27.8 ns of data-path delay does not change it either,
because a 68000 leaves a full bus clock between sampling `~DTACK` and latching the data, and a late
`~DTACK` just buys wait states. Our measured advantage is only at the extremes (27 MHz emulated,
where the model drops 2 phases in 27, and 54 MHz, where it dies).

The gap between that and the field reports of "weak clock-up resistance" is the part simulation
cannot score: **metastability**. midiori feeds unsynchronised `~AS`, `~LDS` and 23 address lines
straight into its FSM, and a simulator resolves those cleanly every time. That is the rule our
design actually earns its keep on; the 108 MHz domain is mostly what makes paying for synchronisers
affordable. See `docs/DECISIONS.md` 2026-10-10.
