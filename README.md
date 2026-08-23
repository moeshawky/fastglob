# Fast Glob

A faster pathname-globbing engine for Linux, compatibility-locked to Python's stdlib `glob`.

**Source:** `src/fastglob/src/lib.rs:1-33` — single-pass port of `/usr/local/lib/python3.12/glob.py` (identified at runtime, never hard-coded)
**Verified:** 2026-08-23 19:03 UTC — `cargo test` 33 passed (16 lib unit + 7 + 10 integration), `make compat` 133/133 PASS, `make test` package 20/20 OK, `clippy -D warnings` clean (see Verification)

> **Seshat Principle:** Aligned to the actual system before writing — every claim cites running code, tests, or captured output.

## Quick Start (Verified — 5 minutes)

**Prerequisites:** Rust >= 1.97 (`src/Cargo.toml:8` `rust-version = "1.97"`; `src/rust-toolchain.toml` pins `channel = "stable"` + rustfmt/clippy), Python — the compatibility **oracle** is the stdlib `glob` of the installed interpreter; this snapshot's capture is locked to 3.12.13 (`/usr/local/bin/python3`) and the freshness gate requires the running interpreter to match exactly, while the Python wrapper itself runs on any Python >= 3.8 (`python/pyproject.toml`), Linux, `libc 0.2` only dependency

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
# Expected: cargo test 33 passed (16 lib unit + 7 + 10 integration) + 133 executed / 133 passed / 0 failed [tests/compat/candidate.json] + python package 20 tests OK
```
**Source:** `tests/fixtures/cases.json` (133 cases), `tests/compat/compare.py:7` (Counter multiset, zone-tolerant)

**Last Verified:** 2026-08-23 19:03 UTC
**Verification Command:** `make test && cargo clippy -- -D warnings && python3 tests/compat/compare.py --self-test`

## Compatibility Scope

Mirrors the **documented contract** (Level A) of the Python stdlib `glob` installed on this machine (`python3 --version` → 3.12.13, `import glob; print(glob.__file__)` → `/usr/local/lib/python3.12/glob.py`), plus stable observable behavior (Level B) where verified. Implementation accidents (Level C) such as `readdir` order are not requirements.

**Source:** `docs/compatibility-contract.md:8-9`, `tests/oracle/capture.json:3-10` (`python 3.12.13, glob_module /usr/local/lib/python3.12/glob.py`)

Covered families (`docs/compatibility-contract.md:8`):
- 8.1 Basic matching (`*`, `?`, `[abc]`, `[a-z]`, `[!abc]`)
- 8.2 Literal path components (including `/`, `//`, `///` → `p_split` byte-exact `walk.rs:99`)
- 8.3 Hidden files (`include_hidden` where supported)
- 8.4 Recursive `**` (zero-or-more dirs, multiplicity preserved, `walk.rs:14-17`)
- 8.5 Symlinks (broken, chain, cycle with 10s timeout guard, `tests/compat/case_runner.py:64-98`)
- 8.6 Filesystem errors (unreadable, disappearing — pruned)
- 8.7 Pathological filenames (spaces, Unicode, newline, byte-exact via `OsStr` end-to-end `lib.rs:14`)
- 8.8 `root_dir` / `dir_fd` / `include_hidden` (`walk.rs:248-368` `listdir`/`scan_fd`)
- 8.9 `escape` / `has_magic` (`matcher.rs:515-543`), `translate` deferred (not exposed)

Ordering is documented-unspecified: tests compare `Counter` multisets, never ordered lists, never plain sets. Duplicate results from overlapping `**` expansions are preserved.

**Verified:** `python3 tests/compat/compare.py --candidate tests/compat/candidate.json` → `133 executed / 133 passed / 0 failed` (plus `zone [zone]` for s08/s09/r12/r13 per §8.5a)

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
```

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
- NUL bytes in PATTERN or `--root-dir` are rejected with exit 2 by the in-process parse (matches Python `ValueError: embedded null byte`; exercised by the cargo tests, `main.rs:149-154`). From a real `execve`, an argv entry is a C string, so a NUL byte truncates the argument BEFORE the binary sees it (**measured 2026-08-23:** `fastglob escape a\0b` → prints `a`, rc 0). The Python wrapper meets this boundary in `subprocess` and raises `ValueError: embedded null byte` for NUL in pattern/root_dir, while the oracle returns `[]` for a NUL pattern and only raises when a NUL reaches `scandir` (e.g. in `root_dir`) — a known MINOR error-boundary divergence on NUL patterns, documented here rather than hidden.
- Patterns longer than 8192 bytes or with more than 512 path components are rejected with exit 2 to bound recursion depth — **verified:** `perl -e 'exec($ARGV[0], "--", "a/"x34133)' fastglob` → `fastglob: pattern too long` rc 2, previous stack overflow SIGABRT -6 fixed (`main.rs:158-164`, `walk.rs:48-51`).
- Symlink cycles are tolerated via kernel ELOOP pruning (Level C, zone-tolerant differential); see `docs/compatibility-contract.md` §8.5a. **Measured:** `fastglob --recursive --root-dir /tmp/cycle '**'` → 41 lines bounded, no hang (10s `SIGALRM` guard `case_runner.py:86-98`).

## Measured Characteristics (Sekel — Ratios, Not Adjectives)

| Claim | Ratio | How to Measure |
|-------|-------|----------------|
| Binary size | 387KB (396280 bytes, stripped, LTO thin, opt-level 3) | `ls -lh src/target/release/fastglob` |
| Build time | 1.4s incremental / 3.9s clean (measured 2026-08-22) | `time make build` |
| Unit tests | cargo test 33 passed (16 lib unit + 7 cli_misuse/cli_boundaries + 10 walk/bin integration) | `cargo test --manifest-path src/Cargo.toml` |
| Compat coverage | 133 cases, 9 families (8.1:15 8.2:13 8.3:14 8.4:15 8.5:16 8.6:8 8.7:17 8.8:18 8.9:17) | `python3 tests/compat/compare.py --candidate tests/compat/candidate.json` |
| Compat result | 133/133 PASS (Counter multiset, `[zone]` for s08/s09/r12/r13) | `make compat` |
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
- **Baseline:** `bench/results/baseline.md` generated on this machine (nproc 224, Python 3.12.13) — run `make bench` to populate, do not copy stale numbers. Previous run 119.8s for wide 100k.
- **Do not** claim speedup from fresh-process benchmark (process-startup win is not engine win) — `bench.py:278` startup rows are labeled separately.

**Verified:** `ls bench/bench.py bench/results/baseline.json` exists 2026-08-22

## Testing

```bash
make test    # cargo test + full compat suite (133 cases, multiset)
make compat  # python3 tests/compat/run_engine.py + compare.py (zone-tolerant for symlink cycles)

cargo test --manifest-path src/Cargo.toml          # 33 passed (16+7+10)
cargo clippy --manifest-path src/Cargo.toml -- -D warnings  # 0 warnings
python3 tests/compat/run_engine.py --out tests/compat/candidate.json  # 133 cases, 0 errors
python3 tests/compat/compare.py --candidate tests/compat/candidate.json  # 133/133 PASS
python3 tests/compat/compare.py --self-test  # 133/133 + 3/3 zone synthetic
```

**Fixture-tree note:** `make compat`/`make oracle-capture` regenerate `tests/fixtures/tree` first (`tests/fixtures/generate.py` — idempotent, byte-deterministic). git cannot preserve the two fixture invariants the freshness gate checks — the `000` mode of `errors/unreadable` and the empty `basic/empty` directory — so a tree restored from a fresh clone would fail the gate; pre-comparison regeneration keeps the invariant.

**Non-root note:** the committed oracle capture was recorded as uid 0; the §8.6 unreadable-dir fixture (and the manifest stamp) are uid-dependent by design. Non-root users: run `make oracle-capture` once before `make compat` (verified 2026-08-23 as uid 65534: 133/133).

**Last Verified:** 2026-08-23 19:03 UTC via `make test`

## Architecture

**Source:** File listing `find . -name "*.rs" -o -name "*.py" | sort` (excluding `target`, `bench/trees`), `grep -rn "use " src/`

- **Engine:** Single-pass Rust port of `glob.py` algorithm (`_iglob → _glob1/_glob2 → _rlistdir → _iterdir`) — `src/fastglob/src/walk.rs:1-22` header, 819 lines, matcher.rs 724 lines, lib.rs 72 lines thin wrapper, main.rs 306 lines CLI. All `&[u8]` byte-exact, `OsStr` end-to-end, no lossy UTF-8.
- **Matcher:** `src/fastglob/src/matcher.rs:43` `decode_chars` surrogateescape `0xDC00|b`, `compile` (:184) fnmatch-3.12 translate port, `matches` (:433) atomic `(?>.*?F)`.
- **Files:** `src/fastglob/src/lib.rs:18-19` `pub mod matcher, walk`; `Cargo.toml` `libc 0.2` only.
- **Python shim:** `python/fastglob/__init__.py:1-327` shells out per call (`subprocess.run`, `pass_fds`, `fsdecode` surrogateescape), never shadows stdlib.
- **Bench/Compat:** `bench/bench.py` 463 lines, `tests/compat/{case_runner.py,run_engine.py,compare.py}` harness with `Counter` + `zone` protocol (`compare.py:64-111`).

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
| `cargo test` 15 passed (missing hidden) | Old `hidden_rules` without `#[test]` | `git pull` — now 33 passed incl. hidden (`walk.rs:639`) |

## Verification (Living Docs)

Run to re-verify this README:
```bash
make build && ./src/target/release/fastglob --help | head -7
cargo test --manifest-path src/Cargo.toml 2>&1 | grep "test result"
python3 tests/compat/compare.py --self-test 2>&1 | tail -3
cargo clippy -- -D warnings 2>&1 | tail -1
ls -lh src/target/release/fastglob
```

**Expected:** `test result: ok` on all three targets (16 + 7 + 10), `133/133 PASS` from `make compat`, `Finished` clippy, `387K`

## Transparent Replacement (planned)

Goal: drop-in `glob` acceleration via `PYTHONPATH`/`.pth`/`sitecustomize` injection with `gnu-glob` escape hatch and auto-rollback on suite failure. Engine → 133/133 → bench/profile → shim order. Not yet implemented; tracked as deferred P1.
**Source:** `AGENTS.md` operator clarification 2026-08-20

## Project Commands (Makefile)

- `make build` — release build (`src/Cargo.toml` LTO thin)
- `make test` — cargo test + compat (133 multiset)
- `make compat` — engine capture + multiset differential vs oracle
- `make bench` — in-process + end-to-end benchmark (fixed ROW order)
- `make profile` — profile primary recursive workload
- `make clean` — remove build artifacts (`cargo clean`)
**Source:** `Makefile:1-35`

---
*Documentation measured 2026-08-23 19:03 UTC from running code `src/target/release/fastglob 396280 bytes`, `python3 --version 3.12.13`, `cargo test 33/33`, `make compat 133/133`, `make test package 20/20`. No adjectives without ratios. Cite `file:line` for every claim.*
