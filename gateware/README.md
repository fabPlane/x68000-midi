# Gateware

Target: Gowin GW1NR-LV9QN88PC6/I5. Prototype on a Tang Nano 9K.

Plan:
1. Port `reference/midiori/gateware/midiori.py` (Migen) to Amaranth. Amaranth's `GowinPlatform` supports both the open toolchain (`toolchain="Apicula"`) and Gowin EDA (`toolchain="Gowin"`).
2. Bring up the transmit-only YM3802 core on the Nano 9K; loop 31 250 bps into the MIDI BOX.
3. Wrap `zf3/psram-tang-nano-9k` (Verilog) as an Amaranth `Instance`; measure random 16-bit read latency.
4. 68000 bus slave with ~DTACK generation and the IRQ vector on ~IACK.
5. YM3802 receive side — last, only when someone needs STed2 recording.
