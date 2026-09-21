# Fast Glob — project commands (docs/final-gate.md §26).
#
#   make build    release-build the engine (src/target/release/fastglob)
#   make test     lint + cargo test + compat + self-test + fused-differential + package + shim
#   make lint     cargo fmt --check + cargo clippy -D warnings (local lint gate)
#   make compat   engine candidate capture + multiset differential vs oracle
#   make oracle-capture  re-capture the stdlib oracle snapshot (freshness gate)
#   make bench    in-process + end-to-end benchmark (bench/bench.py)
#   make profile  profile the engine on the primary recursive workload
#   make clean    remove build artifacts
#   make fused-differential  fused vs recursive differential (tests/test_fused_differential.py)
#   make matcher-differential  matcher differential vs stdlib fnmatch (tests/test_match_bytes_oracle.py)
#   make deploy    gated deployment: runs `make test`, then swaps wheel + shim (DRY_RUN=1 default = plan)
#   make rollback  restore the newest deploy snapshot (DRY_RUN=1 default = plan)
#   make deploy-status  read-only report of repo vs deployed drift (rc 1 when drifted)
#   make deploy-verify  read-only live assertions against the deployed artifacts
#   make deploy-disable  fall back to stdlib: move the shim shadow aside (DRY_RUN=1 default)
#   make wheel     build the distributable wheel (maturin)
#   (deployment driver: tools/deploy.sh — see docs/architecture.md "Deployment")
#
# GATE ALIGNMENT MATRIX (local `make test` vs CI):
#
#   gate                     make test    CI test job    CI python-quality job
#   cargo test                yes          yes            no
#   cargo fmt --check         yes (lint)   yes            no
#   cargo clippy -D warnings  yes (lint)   yes            no
#   cargo doc -D warnings     no*          yes            no
#   cargo deny                no*          yes (if avail) no
#   compat (139 cases)        yes          yes            no
#   compare.py --self-test    yes          yes            no
#   matcher-differential      yes          yes            no
#   fused-differential        yes          yes            no
#   tests/test_package.py     yes          no             yes
#   tests/test_shim_loud.py   yes          yes            no
#   tests/test_shim_parity.py yes          yes            no
#   bash -n tools/deploy.sh   yes          yes            no
#   tests/test_deploy_guard.py yes         yes            no
#   ruff / ruff format        no*          no             yes
#   mypy strict               no*          no             yes
#   pytest-cov                no*          no             yes
#   uv lock --check           no*          no             yes
#   deploy / rollback         n/a          no             no
#
#   deploy/rollback/deploy-status MUTATE (or inspect) system state outside this
#   tree. They are never part of `make test` and never run in CI; `make deploy`
#   is safe by default (DRY_RUN=1 = run the gate, print the plan, stop).
#
#   * CI-ONLY: requires toolchains or extras a plain local `make test` should
#     not require (cargo-deny binary, dev extras for ruff/mypy/pytest-cov).
#     Full quality gate = `make test` + CI python-quality job.
#
# NOTE: compat and oracle-capture regenerate tests/fixtures/tree first
# (tests/fixtures/generate.py). git cannot preserve the fixture invariants the
# oracle capture's freshness gate checks — the 000 mode of errors/unreadable
# and the empty basic/empty directory — so a tree restored from a fresh clone
# would fail the gate. The generator is idempotent and byte-deterministic
# (its manifest hash equals the capture's stamp), so pre-comparison
# regeneration preserves the fixture invariant.

SHELL := /bin/bash
export CARGO_TARGET_DIR := $(abspath src/target)
export FASTGLOB_BIN := $(abspath src/target/release/fastglob)
PY := python3

.PHONY: build test lint compat oracle-capture bench profile clean shim-test fused-differential matcher-differential wheel deploy rollback deploy-status deploy-verify deploy-disable

# Deployment knobs (see tools/deploy.sh). DRY_RUN=1 is the safe default: the
# gate still runs, nothing is mutated. Applying requires DRY_RUN=0.
DRY_RUN ?= 1
SHIM_DIR ?= /opt/fastglob-shim
PYTHONS ?= python3
DEPLOY := DRY_RUN=$(DRY_RUN) SHIM_DIR=$(SHIM_DIR) PYTHONS="$(PYTHONS)"

build:
	cd src && cargo build --release --features pyo3
	# `make compat` imports the source-tree package directly. Build the PyO3
	# cdylib and place it beside __init__.py so a clean checkout does not rely
	# on an accidental ignored `_core.abi3.so` left by a prior wheel build.
	cp src/target/release/libfastglob.so python/fastglob/_core.abi3.so

lint: build
	# Local lint gate: same flags CI uses. Fails fast on first error.
	cargo fmt --check --manifest-path src/Cargo.toml
	cargo clippy --workspace --all-targets --all-features --manifest-path src/Cargo.toml -- -D warnings

test: lint
	cd src && cargo test
	$(MAKE) compat
	# Harness self-test (F-009): CI ran this and `make test` did not, so the
	# comparison logic could rot green locally. Same gate, both places now.
	$(PY) tests/compat/compare.py --self-test
	$(MAKE) matcher-differential
	$(MAKE) fused-differential
	$(PY) tests/test_package.py
	$(MAKE) shim-test
	# The deploy driver is a shipped script: a syntax error in it would surface
	# only on the day someone deploys. Cheap to gate here — and in CI.
	bash -n tools/deploy.sh
	# Rollback authority guards: a snapshot must not be replayed onto targets it
	# never recorded (that mistake deleted a live site-packages install once).
	$(PY) tests/test_deploy_guard.py

fused-differential: build
	# Fused traversal vs recursive differential. Exit 0 on pass.
	$(PY) tests/test_fused_differential.py

matcher-differential: build
	# Matcher differential vs the REAL stdlib fnmatch (committed, reproducible).
	# Replaces the previous unfalsifiable "400k via out/dev/diff_match.py"
	# citation: out/ is gitignored and that file does not exist, so the claim
	# could not be re-executed by anyone reading this repo. Also pins the one
	# declared bytes-mode divergence (docs/compatibility-contract.md §8.10).
	$(PY) tests/test_match_bytes_oracle.py

shim-test: build
	# Ticket 001 item 2: shim routing observability (FASTGLOB_SHIM_LOUD).
	# Loads the shim file from disk in fresh interpreters — no engine or
	# pip install needed beyond `build` (stdlib-fallback verdicts are
	# asserted too). Runs AFTER the package tests so a fastglob import
	# failure surfaces there first, with a clearer error.
	$(PY) tests/test_shim_loud.py
	# Shim NAMESPACE parity (F-001): `from glob import *` must stay satisfiable
	# under the shadow — the shim used to advertise the engine package's
	# `__all__`, which named an unbound `match` and crashed the wildcard import.
	$(PY) tests/test_shim_parity.py
	# Shim-dir path-strip under a SYMLINKED shim dir (2026-09-21): the loader
	# used os.path.abspath, so one shim copy named twice on sys.path (real path
	# + symlink alias) survived the strip and was exec'd as the "real stdlib".
	# Nothing else covers that alias case — the other shim suites name one dir
	# once. Measured red against the abspath loader (2/4 failures).
	$(PY) tests/test_shim_alias_path_strip.py

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

wheel: build
	# Distributable wheel (maturin, abi3-py38) from python/pyproject.toml.
	# Run FROM python/: maturin resolves [tool.maturin] manifest-path relative to
	# the directory holding pyproject.toml, so invoking it from the repo root
	# searches for a repo-root Cargo.toml and fails.
	@command -v maturin >/dev/null 2>&1 || { \
		echo "maturin is required to build the wheel: pip install maturin"; exit 1; \
	}
	mkdir -p dist
	cd python && maturin build --release --out ../dist
	@ls -lh dist/*.whl

# ── Deployment (remedy R4) ────────────────────────────────────────────────
# `deploy` ALWAYS evaluates the gate first — the full `make test` suite — and
# refuses to touch anything if it is red (a red suite must never reach the
# swap). Mutation additionally requires DRY_RUN=0; the default DRY_RUN=1 runs
# the gate and prints the exact plan, then stops. `apply` snapshots both
# artifacts before the first mutation and restores that snapshot automatically
# if any later step fails, so a bad deploy is always recoverable.
#
# Deliberately NOT automated: the PYTHONPATH injection files (provisioner-owned
# "agent-bin-setup": /etc/environment, /etc/profile.d/99-fastglob.sh,
# ~/.bashrc, /opt/agent-bin/bash_noninteractive) and PyPI publishing. Falling
# back to stdlib is done by removing the shadow — an injection path with no
# glob.py resolves to stdlib — which is what `rollback`/`disable` do.
deploy:
	$(DEPLOY) tools/deploy.sh apply

rollback:
	$(DEPLOY) FROM=$(FROM) tools/deploy.sh rollback

deploy-status:
	$(DEPLOY) tools/deploy.sh status

deploy-verify:
	$(DEPLOY) tools/deploy.sh verify

# The stdlib fallback for a box whose shim was NOT deployed from this repo (no
# snapshot to restore): moves $SHIM_DIR/*.py aside to *.disabled-<stamp>, so
# `import glob` resolves to stdlib. `deploy` puts it back.
deploy-disable:
	$(DEPLOY) tools/deploy.sh disable

clean:
	cd src && cargo clean
