PYTHON ?= python3
BUILD_JOBS ?= 8
export PYTHON BUILD_JOBS

.PHONY: check fetch build verify

check:
	shellcheck scripts/*.sh
	shellcheck -s sh tests/init
	$(PYTHON) -m ruff format --check scripts
	$(PYTHON) -m ruff check scripts
	$(PYTHON) -m mypy --strict scripts

fetch build verify: | out

out:
	mkdir -p out

fetch:
	flock out/build.lock $(PYTHON) scripts/inputs.py

build:
	flock out/build.lock bash scripts/build.sh

verify:
	flock --shared out/build.lock $(PYTHON) scripts/verify.py
