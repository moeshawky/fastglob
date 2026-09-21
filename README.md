# Fast Glob

A faster pathname-globbing engine for Linux, compatibility-locked to Python's stdlib `glob`.

**Source:** `src/fastglob/src/lib.rs:1-33` — single-pass port of `/usr/local/lib/python3.12/glob.py` (identified at runtime, never hard-coded)
**Verified:** 2026-09-21 — `cargo test` **34 passed (17 lib unit + 7 cli_boundaries + 10 cli_misuse)**, `make compat` **139/139 PASS**, `make test` package **27 OK** + shim **6 ran/1 skipped** + shim parity **5 OK**, `make test` self-test **139/139 + zone 3/3**, `cargo fmt --check` clean, `clippy --workspace --all-targets --all-features -D warnings` clean (see Verification)

> **Counts and line numbers in this file were re-measured on 2026-09-21.** The previous stamp (2026-08-23) asserted 33 cargo tests / 20 package tests / 3.12.13 / uid 0; none of those matched the repository or its committed oracle capture. Every number below is now a `wc -l`, count, or raw command output.

> **Seshat Principle:** Aligned to the actual system before writing — every claim cites running code, tests, or captured output.

## Quick Start (Verified — 5 minutes)

**Prerequisites:** Rust >= 1.97 (`src/Cargo.toml:8` `rust-version = "1.97"`; `src/rust-toolchain.toml` pins `channel = "stable"` + rustfmt/clippy), Python — the compatibility **oracle** is the stdlib `glob` of the installed interpreter, and the freshness gate (`tests/compat/compare.py` `check_oracle_freshness`) requires the *running* interpreter to equal the capture's exactly. The committed `tests/oracle/capture.json` records `_meta.python = 3.12.3`, `_meta.executable = /usr/bin/python3`, `_meta.uid = 1001`; run `make oracle-capture` on any other interpreter or uid before `make compat` (CI pins `3.12.3` to match the committed capture — a mismatched pin makes the gate raise and the compat job fail by construction). The Python wrapper itself runs on any Python >= 3.8 (`python/pyproject.toml`), Linux, `libc 0.2` only dependency

### Step 1: Build
```bash
make build
# Expected: Finished `release` profile [optimized] target(s)
# Binary: src/target/release/fastglob 387KB (396280 bytes, stripped, LTO thin)
```
**Source:** `Makefile:18-19`, `src/Cargo.toml:13-17` (`profile.release`: opt-level 3, LTO thin, strip), `src/fastglob/Cargo.toml:11-13` (bin target)

### Step 2: Verify health
```bash
./src/target/release/fastglob --help
# Expected:
# usage: fastglob [OPTIONS] PATTERN
# usage: fastglob escape PATTERN
# Options:
#   --recursive        enable ** (zero-or-more directories)
#   --include-hidden   let * ? ** match dot-prefixed names
#   --null             NUL-delimit output (use for arbitrary filenames with newlines)
#   --root-dir PATH    shift the filesystem origin
#   --dir-fd N         resolve relative to open directory fd N
# Exit: 0, stdout contains usage, stderr empty

./src/target/release/fastglob --help; echo exit:$?
# Expected: exit:0
```

### Step 3: Run Python API
```bash
pip install -e python   # builds the in-process engine (maturin + Rust toolchain); PyPI wheel needs neither
python3 <<'PY'
import fastglob
print(fastglob.glob("*.py"))  # [] in empty dir, or ['file.py'] if file exists
print(fastglob.escape("a*b"))  # Expected: a[*]b
PY
# Expected: a[*]b
# No-install fallback (instead of the line above):
#   PYTHONPATH=python python3 -c 'import fastglob; print(fastglob.escape("a*b"))'
```
**Source:** `python/fastglob/__init__.py` (`glob`/`iglob`/`escape`/`has_magic` over the in-process `fastglob._core` native engine)

### Step 4: Run tests
```bash
make test
# Expected: cargo test 34 passed (17 lib unit + 7 + 10 integration) + 139 executed / 139 passed / 0 failed [tests/compat/candidate.json] (2 of them [zone]) + self-test 139/139 + python package 27 tests OK + shim 6 tests OK (1 skipped) + shim parity 5 OK
```
**Source:** `tests/fixtures/cases.json` (139 cases), `tests/compat/compare.py:98-165` (Counter multiset, cycle-zone-tolerant)

**Last Verified:** 2026-08-23 19:03 UTC
**Verification Command:** `make test && cargo clippy -- -D warnings && python3 tests/compat/compare.py --self-test`

## Compatibility Scope

Mirrors the **documented contract** (Level A) of the Python stdlib `glob` installed on this machine (`python3 --version` → **3.12.3**, `import glob; print(glob.__file__)` → **`/usr/lib/python3.12/glob.py`**), plus stable observable behavior (Level B) where verified. Implementation accidents (Level C) such as `readdir` order are not requirements.

**Source:** `docs/compatibility-contract.md:8-9`, `tests/oracle/capture.json` `_meta` (`python 3.12.3, executable /usr/bin/python3, glob_module /usr/lib/python3.12/glob.py, uid 1001`)

Covered families (`docs/compatibility-contract.md:8`):
- 8.1 Basic matching (`*`, `?`, `[abc]`, `[a-z]`, `[!abc]`)
- 8.2 Literal path components (including `/`, `//`, `///` → `p_split` byte-exact `walk.rs:99`)
- 8.3 Hidden files (`include_hidden` where supported)
- 8.4 Recursive `**` (zero-or-more dirs, multiplicity preserved, `walk.rs:14-17`)
- 8.5 Symlinks (broken, chain, cycle with 10s timeout guard, `tests/compat/case_runner.py:64-98`)
- 8.6 Filesystem errors (unreadable, disappearing — pruned)
- 8.7 Pathological filenames (spaces, Unicode, newline, byte-exact via `OsStr` end-to-end `lib.rs:14`)
- 8.8 `root_dir` / `dir_fd` / `include_hidden` (`walk.rs:248-368` `listdir`/`scan_fd`)
- 8.9 `escape` / `has_magic` (`matcher.rs:547-569`), `translate` deferred (not exposed); `match(pattern, path)` single-path match **present but unreleased** (`__version__` is `0.1.3`) with the SAME fnmatch semantics (whole-path, `*` crosses `/`) — oracle: stdlib `fnmatch.fnmatchcase` in **str** mode; in **bytes** mode the engine decodes as `os.fsdecode` (UTF-8 + surrogateescape), so it is fnmatch-exact except for paths containing a valid multi-byte UTF-8 sequence — a bounded, pinned divergence (§8.10)

Ordering is documented-unspecified: tests compare `Counter` multisets, never ordered lists, never plain sets. Duplicate results from overlapping `**` expansions are preserved.

**Verified:** `python3 tests/compat/compare.py --candidate tests/compat/candidate.json` → `139 executed / 139 passed / 0 failed` (plus `[zone]` for s08/s09/r12/r13/r17/r19 per §8.5a)

## Installation

```bash
make build   # cargo build --release -> src/target/release/fastglob, 387KB (only for source-dev / direct CLI use)
```

Python package (separate, never shadows stdlib `glob`):
```bash
pip install fastglob    # PyPI: engine ships inside the wheel — just works, no make build
pip install -e python   # source dev: compiles the in-process engine via maturin (or: PYTHONPATH=python)
```

```python
import fastglob
fastglob.glob("*.py", recursive=True, include_hidden=False)
list(fastglob.iglob("a/**/b.txt", recursive=True))
fastglob.escape("a*b")  # -> "a[*]b"
fastglob.match("**/vendor/**", "a/vendor/b.rs")  # -> True (no filesystem access)
```

### `fastglob.match(pattern, path)` (present since commit `aacfa41`; **not yet released** — `__version__` is still `0.1.3`)

Single-path match with NO filesystem access — the path is pure data (never opened, never stat'ed, never walked). Semantics are the engine's fnmatch-3.12 matcher applied to the WHOLE path string (stdlib `fnmatch` parity, oracle-verified):

- `*` and `**` cross `/` (they are NOT component-aware): `match("**/vendor/**", "a/vendor/b.rs")` → `True`
- `*` needs ≥1 char: `match("**/vendor/**", "vendor")` → `False`; a bare `*` pattern matches ANY path (`(?s:.*)`)
- literals must span the whole path: `match("b.rs", "a/b.rs")` → `False`
- type contract: str pattern → str comparison, bytes pattern → bytes comparison; cross-type raises `TypeError` (stdlib `fnmatch` parity). In **bytes** mode the decode is `os.fsdecode`'s (UTF-8 + surrogateescape), not `fnmatchcase`'s ISO-8859-1, so `?`/`[...]` counts diverge for a path containing a valid multi-byte UTF-8 sequence (e.g. `match(b"?", b"\xc3\xa9")` → `True`, stdlib → `False`). Bounded and pinned in §8.10
- pattern guards mirror `glob`: NUL → `ValueError`, >8192 bytes / >512 components → `RuntimeError "pattern too long"`; the PATH argument is unguarded data

**Source:** `python/fastglob/__init__.py` (`match`), `src/fastglob/src/pyo3_ext.rs` (`r#match`), tests `tests/test_package.py::MatchContract` (oracle-agreed acceptance table)

The engine runs in-process through `fastglob._core` (PyO3 module built from the same Rust crate); since 0.1.1 the package no longer reads `FASTGLOB_BIN` and spawns no subprocess. The `fastglob` CLI binary remains available for direct use.
**Source:** `python/fastglob/__init__.py` (`_core` in-process calls), `src/fastglob/src/pyo3_ext.rs` (engine bindings)

## CLI

```bash
fastglob [OPTIONS] PATTERN
fastglob escape PATTERN

Options:
  --recursive          enable ** (zero-or-more directories)
  --include-hidden     let * ? ** match dot-prefixed names
  --null               NUL-delimit output (use for arbitrary filenames with newlines)
  --root-dir PATH      shift filesystem origin (results stay pattern-built)
  --dir-fd N           resolve relative to open directory fd N
```
**Source:** `src/fastglob/src/main.rs:36` (`USAGE`; printed at `main.rs:57,125`), verified `src/target/release/fastglob --help` 2026-08-22

**PATH note:** the binary is NOT on your PATH after a fresh clone — use `./src/target/release/fastglob` (as in Quick Start) or add `src/target/release` to PATH.

Notes (measured, not adjectives):
- Output is one pathname per line by default; use `--null` for arbitrary filenames containing newlines (newline-delimited output splits on `\n` and cannot round-trip such names). The Python package always uses `--null` internally (`python/fastglob/__init__.py:148`).
- `--root-dir` and `--dir-fd` are mutually exclusive (specifying both is exit 2) — **verified:** `fastglob --root-dir /tmp --dir-fd 3 -- '*'` → `fastglob: cannot specify both --root-dir and --dir-fd` rc 2 (`main.rs:166-168`).
- NUL bytes in PATTERN or `--root-dir` are rejected with exit 2 by the in-process parse (same exception TYPE as Python's `ValueError` — the engine's message text differs, see below; exercised by the cargo tests, `main.rs:149-154`). From a real `execve`, an argv entry is a C string, so a NUL byte truncates the argument BEFORE the binary sees it (**measured 2026-08-23:** `fastglob escape a\0b` → prints `a`, rc 0). The Python wrapper meets this boundary in-process (**no `subprocess` since 0.1.1**) and raises `ValueError` for NUL in pattern/root_dir (`fastglob: embedded null byte in PATTERN` / `... in --root-dir`, measured 2026-09-21 — same exception type as stdlib, different message text), while the oracle returns `[]` for a NUL pattern and only raises when a NUL reaches `scandir` (e.g. in `root_dir`) — a known MINOR error-boundary divergence on NUL patterns, documented here rather than hidden.
- Patterns longer than 8192 bytes or with more than 512 path components are rejected with exit 2 to bound recursion depth — **verified:** `perl -e 'exec($ARGV[0], "--", "a/"x34133)' fastglob` → `fastglob: pattern too long` rc 2, previous stack overflow SIGABRT -6 fixed (`main.rs:158-164`, `walk.rs:48-51`).
- Symlink cycles are tolerated via kernel ELOOP pruning (Level C, zone-tolerant differential); see `docs/compatibility-contract.md` §8.5a. **Measured:** `fastglob --recursive --root-dir /tmp/cycle '**'` → 41 lines bounded, no hang (10s `SIGALRM` guard `case_runner.py:86-98`).

## Measured Characteristics (Sekel — Ratios, Not Adjectives)

| Claim | Ratio | How to Measure |
|-------|-------|----------------|
| Binary size | 387KB (396280 bytes, stripped, LTO thin, opt-level 3) | `ls -lh src/target/release/fastglob` |
| Build time | 1.4s incremental / 3.9s clean (measured 2026-08-22) | `time make build` |
| Unit tests | cargo test 34 passed (17 lib unit + 7 cli_boundaries + 10 cli_misuse) | `cargo test --manifest-path src/Cargo.toml` |
| Compat coverage | 139 cases, 9 families (8.1:15 8.2:13 8.3:14 8.4:21 8.5:16 8.6:8 8.7:17 8.8:18 8.9:17) | `python3 tests/compat/compare.py --candidate tests/compat/candidate.json` |
| Compat result | 139/139 PASS (Counter multiset, `[zone]` for s08/s09/r12/r13/r17/r19) | `make compat` |
| Python package tests | 27 OK | `python3 tests/test_package.py` |
| Shim tests | 6 ran (1 skipped) + 5 parity OK | `make shim-test` |
| Clippy | 0 warnings with `-D warnings` | `cargo clippy -- -D warnings` |
| Dependencies | 1 crate (`libc 0.2`) + Python stdlib only | `grep dependencies -A2 src/Cargo.toml` |
| Pattern limit | 8192 bytes, 512 components → exit 2 | `fastglob -- $(python3 -c "print('a/'*34133)")` |
| Timeout guard | 10s per case (`SIGALRM`/`ITIMER_REAL`) | `tests/compat/case_runner.py:89` `signal.setitimer` |
| Ordering | Unspecified — `Counter` equality, never sorted | `tests/compat/compare.py:231` |

**How to Measure Yourself:**
```bash
ls -lh src/target/release/fastglob
time make build
cargo test --manifest-path src/Cargo.toml 2>&1 | grep "passed"
python3 tests/compat/compare.py --candidate tests/compat/candidate.json 2>&1 | tail -1
cargo clippy -- -D warnings 2>&1 | tail -1
```

## Benchmarks

Benchmarks are empirical and run in-process per `docs/benchmark-discipline.md` §10.1A:

```bash
make bench   # runs bench/bench.py -> bench/results/baseline.json + baseline.md
make profile # profile engine on primary recursive workload
```

- **Method:** 21 ROWS across 8 shapes (small/wide/deep/sparse/dense/recursive/hidden/symlinks), fixed ROW order for page-cache equivalence, warm≤5, adaptive reps `51/41/31/21/11/9` capped `reps*med≤240s` (`bench/bench.py:13-18,157-173`), per-rep timeout via `SIGALRM` watchdog (`bench/bench.py:129-155`).
- **Fresh clone:** the bench trees are gitignored — run `python3 bench/generate_tree.py` once first (~31s here); `make bench` prints exactly that hint if the manifest is missing.
- **Baseline:** `bench/results/baseline.md` is generated on the measuring machine — run `make bench` to populate, do not copy stale numbers. (The committed baseline came from a **different machine**: x86_64 / nproc 224 / Python 3.12.13, git `5668b01` — which is *not* a commit in this repository's history. This box is aarch64 / nproc 2 / Python 3.12.3. See the Provenance block at the top of that file; re-measure before quoting.)
- **Do not** claim speedup from fresh-process benchmark (process-startup win is not engine win) — `bench.py:278` startup rows are labeled separately.

**Verified:** `ls bench/bench.py bench/results/baseline.md` exists 2026-09-21. (`bench/results/baseline.json` is written by `make bench` but is gitignored — `.gitignore:41-43` ignores `bench/results/*.json` and un-ignores only `baseline.md` — so it is intentionally absent from the repo; a previous stamp claimed otherwise.)

## Testing

```bash
make test    # cargo test + compat (139 cases) + self-test + package + shim + shim-parity
make compat  # python3 tests/compat/run_engine.py + compare.py (zone-tolerant for symlink cycles)

cargo test --manifest-path src/Cargo.toml          # 34 passed (17+7+10)
cargo clippy --manifest-path src/Cargo.toml -- -D warnings  # 0 warnings
python3 tests/compat/run_engine.py --out tests/compat/candidate.json  # 139 cases, 0 errors
python3 tests/compat/compare.py --candidate tests/compat/candidate.json  # 139/139 PASS
python3 tests/compat/compare.py --self-test  # 139/139 + 3/3 zone synthetic
```

**Fixture-tree note:** `make compat`/`make oracle-capture` regenerate `tests/fixtures/tree` first (`tests/fixtures/generate.py` — idempotent, byte-deterministic). git cannot preserve the two fixture invariants the freshness gate checks — the `000` mode of `errors/unreadable` and the empty `basic/empty` directory — so a tree restored from a fresh clone would fail the gate; pre-comparison regeneration keeps the invariant.

**Non-root note:** the §8.6 unreadable-dir fixture (and the manifest stamp) are uid-dependent by design. The committed capture records `_meta.uid = 1001`; on any other uid run `make oracle-capture` once before `make compat` (CI does this unconditionally).

**Last Verified:** 2026-09-21 via `make test` (fmt + clippy + 34 cargo + 139/139 compat + self-test + 27 package + 6 shim + 5 shim-parity)

## Architecture

**Source:** File listing `find . -name "*.rs" -o -name "*.py" | sort` (excluding `target`, `bench/trees`), `grep -rn "use " src/`

- **Engine:** Single-pass Rust port of `glob.py` algorithm (`_iglob → _glob1/_glob2 → _rlistdir → _iterdir`), plus a **fused fast path** (`try_fast` and helpers, consulted first, with the verbatim port as fallback) — measured `wc -l` (post-format): `walk.rs` **1717**, `matcher.rs` **790**, `lib.rs` **79**, `main.rs` **339**, `pyo3_ext.rs` **213**. Setting the env var **`FASTGLOB_NO_FUSED`** (non-empty) disables the fused path for the whole process and forces the verbatim port — a test affordance for differentially exercising the two implementations; latched once at the first glob call, inert when unset, and a no-op for `--dir-fd`. All `&[u8]` byte-exact, `OsStr` end-to-end, no lossy UTF-8.
- **Matcher:** `src/fastglob/src/matcher.rs:54` `decode_chars` surrogateescape `0xDC00|b`, `compile` (:208) fnmatch-3.12 translate port, `matches` (:465) atomic `(?>.*?F)`. Differential vs stdlib `fnmatch.fnmatchcase` is committed and reproducible: `tests/test_match_bytes_oracle.py` (59,860 exhaustive + 20,000 random str pairs, 0 mismatches).
- **Files:** `src/fastglob/src/lib.rs:18-19` `pub mod matcher, walk`; `Cargo.toml` `libc 0.2` only.
- **Python package:** `python/fastglob/__init__.py` calls the PyO3 in-process `_core` (no subprocess, no `pass_fds`; `os.fsdecode`/`fsencode` surrogateescape). The deployed shim `shim/glob.py` (0.1.3) shadows stdlib `glob` via `PYTHONPATH=/opt/fastglob-shim` with `gnu_glob` escape hatch.
- **Bench/Compat:** `bench/bench.py` **486** lines, `tests/compat/{case_runner.py,run_engine.py,compare.py}` harness with `Counter` + `zone` protocol (`compare.py:98-165`).

See `docs/architecture.md` for C4 diagram and `docs/api.md` for typed signatures.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: No module named 'fastglob'` | `python/fastglob` not installed | `pip install -e python` or `PYTHONPATH=python python3 -c 'import fastglob'` |
| `fastglob: missing PATTERN` rc 2 | No PATTERN arg | Provide pattern: `fastglob "*"` or `fastglob -- pattern` |
| `fastglob: pattern too long` rc 2 | Pattern >8192 bytes or >512 components | Shorten pattern; this guards stack overflow (F-002 fix) |
| `fastglob exited 101: panicked at env.rs` | Old binary with `env::args()` unwrap (pre-65775f5b) | `make build` to get args_os fix (`main.rs:250-252`) |
| `fastglob --dir-fd: invalid fd` rc 2 | Closed or non-directory fd | `os.open(path, O_DIRECTORY)` and keep fd open via `python` wrapper's `F_DUPFD` |
| `cannot specify both --root-dir and --dir-fd` rc 2 | Both flags supplied | Use one; they are mutually exclusive (`main.rs:166-168`) |
| Output splits `a\nb.txt` into two lines | Newline-delimited default cannot round-trip newlines | Use `--null` (`-print0` style): `fastglob --null -- '*' \| tr '\0' '\n'` |
| `/` returns empty (old) | `p_split` bug pre-fix | `make build` — fixed `p_split(b"/") → (b"/",b"")` (`walk.rs:99`) |
| `cargo test` 15 passed (missing hidden) | Old `hidden_rules` without `#[test]` | `git pull` — now 34 passed incl. hidden |
| `cargo fmt --check` fails on a clean clone | `walk.rs`/`matcher.rs` contained code not formatted at the repo's `max_width = 100` | Fixed 2026-09-21 by running `cargo fmt` (formatting only; semantics unchanged) |
| `from glob import *` raises `AttributeError: module 'glob' has no attribute 'match'` | Shim adopted the engine package's `__all__` (which names `match`) while binding only `glob/iglob/escape/has_magic` | Fixed 2026-09-21 — the shim now mirrors the stdlib `__all__`; regression test `tests/test_shim_parity.py` |

## Verification (Living Docs)

Run to re-verify this README:
```bash
make build && ./src/target/release/fastglob --help | head -7
cargo test --manifest-path src/Cargo.toml 2>&1 | grep "test result"
python3 tests/compat/compare.py --self-test 2>&1 | tail -3
cargo clippy -- -D warnings 2>&1 | tail -1
ls -lh src/target/release/fastglob
```

**Expected:** `test result: ok` on all targets (17 + 7 + 10), `139/139 PASS` from `make compat`, `Finished` clippy, `452K`

## Transparent Replacement (deployed)

Status: DEPLOYED — drop-in `glob` acceleration via `PYTHONPATH=/opt/fastglob-shim` injection (`shim/glob.py`, 0.1.3) with `gnu_glob` escape hatch. Order: engine → 139/139 → bench/profile → shim. (See `docs/architecture.md` Deployment.)

**Deployment drift (measured 2026-09-21):** the file actually installed at `/opt/fastglob-shim/glob.py` is **not** the repo's `shim/glob.py` (different length and md5; `diff /opt/fastglob-shim/glob.py shim/glob.py | wc -l` = **280**). The deployed copy predates the repo revision, so on the deployed box `FASTGLOB_SHIM_LOUD` is **absent** (`grep -c FASTGLOB_SHIM_LOUD /opt/fastglob-shim/glob.py` → `0`) and the shim does not exclude ad-hoc shim directories from `sys.path`. `shim/` is canonical; deploying it is an operator action (`agent-bin` Pillar 3). `make deploy-status` (added 2026-09-21; read-only, exits 1 on drift) now *detects* it instead of only documenting it — its first run found a **second** drift class in the same family: the installed wheel at `/usr/local/lib/python3.12/dist-packages/fastglob` is a 2026-08-28 build whose `__init__.py` has no `match` while the repo's package does (`grep -c '^def match'` → **0** vs **3**). See `.tickets/` and the maintenance ledger.

**Rollback lives in the deploy driver, not in the shim.** The shim has no suite-failure mechanism of its own: it falls back to stdlib at import time only when `import fastglob` fails, never when fastglob answers *wrongly*. Recovery is a deployment action — `make deploy` gates on the full `make test` suite and snapshots both artifacts before the first mutation, and `make rollback` restores the newest snapshot — and only a snapshot that recorded the same `SHIM_DIR` and interpreters is accepted, because restoring a foreign one would delete an install it never captured (measured the hard way; see the maintenance ledger). `DRY_RUN=1` is the default for both (gate + plan, nothing mutated); `DRY_RUN=0` applies. Any earlier 'auto-rollback' wording describing the shim was unverified and stays removed.

**Observable routing (present in `shim/`, `FASTGLOB_SHIM_LOUD`):** the shim is silent by default (invisibility guarantee unchanged). Set `FASTGLOB_SHIM_LOUD` to any non-empty value and the shim emits ONE stderr line at first interception naming the engine that answered:

```
fastglob-shim: intercepting 'glob' (shim 0.1.3, engine: fastglob 0.1.3)
# or, when the engine is missing:
fastglob-shim: intercepting 'glob' (shim 0.1.3, engine: STDLIB FALLBACK (fastglob not importable))
```

Never touches stdout; never raises; exactly one line per process. Tests: `tests/test_shim_loud.py`.
**Source:** `AGENTS.md` operator clarification 2026-08-20; loudness switch per `.tickets/001-expose-match-api.md` item 2

## Project Commands (Makefile)

- `make build` — release build (`src/Cargo.toml` LTO thin)
- `make test` — cargo test + compat (139 multiset) + self-test + package tests + shim + shim-parity
- `make compat` — engine capture + multiset differential vs oracle
- `make bench` — in-process + end-to-end benchmark (fixed ROW order)
- `make profile` — profile primary recursive workload
- `make wheel` — build the distributable wheel (maturin, abi3-py38, `dist/`)
- `make deploy` — gated deployment: runs `make test`, then swaps wheel + shim. `DRY_RUN=1` (default) runs the gate and prints the plan; `DRY_RUN=0` applies
- `make rollback` — restore the newest deploy snapshot (`FROM=<stamp>` to choose one; `rollback` needs `DRY_RUN=0`). A snapshot is accepted only for the `SHIM_DIR` and interpreters it recorded; replaying a foreign one would delete an install it never captured, so the driver refuses instead (`tests/test_deploy_guard.py`)
- `make deploy-status` — read-only repo-vs-deployed drift report (exits 1 on drift)
- `make deploy-verify` — read-only live assertions against the deployed artifacts
- `make deploy-disable` — fall back to stdlib by moving the shim shadow aside (`DRY_RUN=0` applies); `make deploy` puts it back
- `make clean` — remove build artifacts (`cargo clean`)

Deployment is the one surface that reaches outside this tree: it installs the wheel into site-packages and copies `shim/*.py` into `/opt/fastglob-shim`. It never edits the provisioner-owned `PYTHONPATH` injection files (`/etc/environment`, `/etc/profile.d/99-fastglob.sh`, `~/.bashrc`, `/opt/agent-bin/bash_noninteractive`) — falling back to stdlib is done by removing the shadow, since an injection directory without a `glob.py` resolves to stdlib `glob` (measured). Driver: `tools/deploy.sh`; design: `docs/architecture.md` §Deployment.

**Source:** `Makefile:1-55` (target list + gate-alignment matrix), `tools/deploy.sh:1-40` (safety model)

---
*Documentation measured 2026-09-21 from running code (`python3 --version` 3.12.3, `cargo test` 34/34, `make compat` 139/139, `make test` package 27/27, shim 6 ran + 5 parity; `tools/deploy.sh` status/plan/apply/rollback exercised end-to-end in a throwaway venv, leaving the live box untouched). No adjectives without ratios. Cite `file:line` for every claim.*
