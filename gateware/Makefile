# X68000 MIDI gateware. Layout and toolchain mirror vodoo_2/amaranth.
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
RUN := $(UV) run --project $(CURDIR)
RTL_DIR := $(CURDIR)/rtl

.PHONY: help venv test rtl-module clean

help:
	@echo "venv                 uv sync (create/refresh .venv)"
	@echo "test                 pytest (pysim)"
	@echo "rtl-module MOD=UartTx   elaborate one module -> rtl/<MOD>.v"

venv:
	$(UV) sync --project $(CURDIR)

test: venv
	$(RUN) pytest

rtl-module: venv
	@test -n "$(MOD)" || { echo "usage: make rtl-module MOD=<Name>"; exit 2; }
	$(RUN) python -m gen.gen_module $(MOD) -o $(RTL_DIR) $(PARAMS)

clean:
	rm -rf $(RTL_DIR) .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
