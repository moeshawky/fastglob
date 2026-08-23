# Fast Glob — project commands (docs/final-gate.md §26).
#
#   make build    release-build the engine (src/target/release/fastglob)
#   make test     cargo test + full compat suite
#   make compat   engine candidate capture + multiset differential vs oracle
#   make oracle-capture  re-capture the stdlib oracle snapshot (freshness gate)
#
# NOTE: compat and oracle-capture regenerate tests/fixtures/tree first
# (tests/fixtures/generate.py). git cannot preserve the fixture invariants the
# oracle capture's freshness gate checks — the 000 mode of errors/unreadable
# and the empty basic/empty directory — so a tree restored from a fresh clone
# would fail the gate. The generator is idempotent and byte-deterministic
# (its manifest hash equals the capture's stamp), so pre-comparison
# regeneration preserves the fixture invariant.
#   make bench    in-process + end-to-end benchmark (bench/bench.py)
#   make profile  profile the engine on the primary recursive workload
#   make clean    remove build artifacts

SHELL := /bin/bash
export CARGO_TARGET_DIR := $(abspath src/target)
export FASTGLOB_BIN := $(abspath src/target/release/fastglob)
PY := python3

.PHONY: build test compat oracle-capture bench profile clean

build:
	cd src && cargo build --release

test: build
	cd src && cargo test
	$(MAKE) compat
	$(PY) tests/test_package.py

compat: build
	$(PY) tests/fixtures/generate.py
	$(PY) tests/compat/run_engine.py --out tests/compat/candidate.json
	$(PY) tests/compat/compare.py --candidate tests/compat/candidate.json

oracle-capture:
	$(PY) tests/fixtures/generate.py
	$(PY) tests/oracle/capture.py

bench: build
	$(PY) bench/bench.py

profile: build
	$(PY) bench/bench.py --profile

clean:
	cd src && cargo clean
