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
| YM3802 transmit FIFO + ITX (active-sensing 0xFE) wrapper | next | midiori `Midiori` |
| 68000 bus slave, register file, IRQ vector | next | midiori `Midiori` |
| PSRAM window | later | zf3/psram-tang-nano-9k (Verilog `Instance`) |
| YM3802 receive side | last | new |

## Plan

1. Port `reference/midiori/gateware/midiori.py` (Migen) to Amaranth, module by module, each with a pysim test — no board needed.
2. Transmit core + a small in-FPGA 68000-bus stimulus; loop 31 250 bps into the MIDI BOX. Console first (Gowin EDA), then Nano 9K (apicula).
3. **Slot breakout PCB**: 2 × 50 fingers + 6 translators + 2 × 20 header, no FPGA. Proves the finger footprint, bevel, plate and card guides cheaply, and connects either dev board to a real X68000 for the bus-slave bring-up (~DTACK timing, IRQ vector on ~IACK, IPL sizing with a BSRAM/DDR3 stand-in window).
4. Nano 9K: wrap `zf3/psram-tang-nano-9k` as an `Instance`; measure random 16-bit read latency; find the PSRAM pad placement for the open flow.
5. YM3802 receive side — last, only when someone needs STed2 recording.
