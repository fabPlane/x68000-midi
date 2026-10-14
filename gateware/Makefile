# X68000 MIDI gateware. Layout and toolchain mirror vodoo_2/amaranth.
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
RUN := $(UV) run --project $(CURDIR)
RTL_DIR := $(CURDIR)/rtl

# Synthesis and place-and-route use YosysHQ's oss-cad-suite, because the
# himbaechel gowin target is not in homebrew (only nextpnr-ice40 is).
OSS_CAD ?= $(HOME)/tools/oss-cad-suite
OSS_BIN := $(OSS_CAD)/bin
# GW1NR-9 is a GW1N-9C die with 8 MB PSRAM in the package.
DEVICE  ?= GW1NR-LV9QN88PC6/I5
FAMILY  ?= GW1N-9C
FREQ    ?= 108
BUILD   := $(CURDIR)/build
# The board's own pinout cannot be used yet: Card exposes data_i/data_o
# separately where the board has one bidirectional pin per data line, and the
# board clocks 27 MHz where the design wants 108. Override CST once a
# board-level wrapper exists.
CST     ?= $(BUILD)/throwaway.cst

.PHONY: help venv test rtl-module clean synth pnr

help:
	@echo "venv                 uv sync (create/refresh .venv)"
	@echo "test                 pytest (pysim)"
	@echo "rtl-module MOD=UartTx   elaborate one module -> rtl/<MOD>.v"
	@echo "synth                yosys synth_gowin -> build/Card.json"
	@echo "pnr                  nextpnr-himbaechel for the GW1NR-9 + timing"

venv:
	$(UV) sync --project $(CURDIR)

test: venv
	$(RUN) pytest

rtl-module: venv
	@test -n "$(MOD)" || { echo "usage: make rtl-module MOD=<Name>"; exit 2; }
	$(RUN) python -m gen.gen_module $(MOD) -o $(RTL_DIR) $(PARAMS)

synth: venv
	@test -x $(OSS_BIN)/yosys || { echo "no yosys at $(OSS_BIN); set OSS_CAD"; exit 2; }
	$(RUN) python -m gen.gen_module Card -o $(RTL_DIR)
	mkdir -p $(BUILD)
	$(OSS_BIN)/yosys -p "read_verilog $(RTL_DIR)/Card.v; \
		synth_gowin -top Card -json $(BUILD)/Card.json" | tee $(BUILD)/yosys.log

pnr: synth
	$(OSS_BIN)/nextpnr-himbaechel --json $(BUILD)/Card.json \
		--write $(BUILD)/Card_pnr.json --device "$(DEVICE)" \
		--vopt family=$(FAMILY) --vopt cst=$(CST) --freq $(FREQ) \
		2>&1 | tee $(BUILD)/pnr.log
	@grep -E "Device utilisation|LUT4:|DFF:|BSRAM:|IOB:" $(BUILD)/pnr.log || true
	@grep -E "Max frequency for clock" $(BUILD)/pnr.log || true

clean:
	rm -rf $(RTL_DIR) .pytest_cache $(BUILD)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
