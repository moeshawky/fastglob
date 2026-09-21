# Fast Glob — Architecture

**Measured:** 2026-08-22 23:46 UTC via `find`, `grep`, `cargo`, `ls`
**Source:** Running code `src/fastglob/src/*.rs` (1921 lines total), `python/fastglob/__init__.py:1-327`, `bench/`, `tests/`

---

## Context

**Audience:** Operators deploying the `agent-bin-setup` family (`find→bfs`, `grep→ugrep`, `glob→fastglob`).
**Intent:** Transparent, faster, behaviorally identical `glob` — agents call `glob` and notice nothing (AGENTS.md operator clarification 2026-08-20).

**System Boundary:** Only programmatic pattern expansion (Python `glob`, `glob(3)`), not shell expansion (`echo *.py` expands before binary).

---

## Container Diagram (Verified via `find` + `Makefile`)

```
[Agent Python code] --import fastglob--> [python/fastglob/__init__.py] --PyO3 in-process _core--> [Rust engine fastglob._core (maturin/PyO3, 0.1.4)]
         |                                                                                         |
         |--- stdlib fallback (gnu-glob escape hatch, planned)                                     |--- filesystem (readdir, fstatat, openat)
         |--- tests/fixtures/tree (139 cases, 9 families)                                        |--- matcher fnmatch-3.12 (surrogateescape)
```

**File Structure (measured `find . -name "*.rs" -o -name "*.py" | grep -v target | sort`):**
```
src/Cargo.toml (workspace, libc 0.2 only; profile.release: opt-level 3, LTO thin, strip)
src/fastglob/Cargo.toml (lib+bin, edition 2021, rust-version 1.97)
src/fastglob/src/lib.rs 79 lines (thin OsStr wrapper)
src/fastglob/src/main.rs 339 lines (CLI parse, args_os, validate_dir_fd)
src/fastglob/src/matcher.rs 790 lines (fnmatch-3.12 port, decode_chars, compile)
src/fastglob/src/walk.rs 1717 lines (single-pass walk, p_split/p_join, cstr, listdir/scan_fd, _iglob/_glob1/_glob2/rlistdir) + the FUSED FAST PATH (try_fast and helpers, consulted first; verbatim port is the fallback; `FASTGLOB_NO_FUSED` disables it — test affordance, latched once) + pyo3_ext.rs 213 lines (module fastglob._core)
python/fastglob/__init__.py 465 lines (in-process PyO3 _core calls, fsencode/fsdecode surrogateescape, bytes|str type preservation — subprocess shell-out and F_DUPFD were removed in 0.1.1)
bench/bench.py 486 lines, bench/generate_tree.py, bench/trees/{wide,deep,sparse,dense}
tests/fixtures/generate.py, tests/fixtures/cases.json (139), tests/oracle/capture.py, tests/compat/{case_runner.py,run_engine.py,compare.py}
docs/{compatibility-contract,benchmark-discipline,failure-modes,final-gate} (doctrine)
```

**Dependencies (verified `grep -rn "use " src/` + `Cargo.toml`):**
| From | To | Protocol | Verified |
|------|----|----------|----------|
| `python/fastglob/__init__.py` | `fastglob._core` (PyO3, in-process) | direct `_core.glob(...)` call — no process spawn | `__init__.py:141` |
| `walk.rs` | Kernel | `fdopendir/readdir/closedir`, `openat`, `fstatat`, `lseek` | `walk.rs:389,395,409`, `:293,309`, `:215,234,255`, `:310-323` |
| `matcher.rs` | `walk.rs` | `Program` struct, `has_magic`, `compile` | `walk.rs:429,453,462-464,494,500` |
| `main.rs` | `walk.rs` | `walk::glob(pattern, root_dir, dir_fd, opts)` | `main.rs:310` |
| `lib.rs` | `walk/matcher` | `OsStr` wrapper, `pub mod` | `lib.rs:23,26,42-43` |

- **Only crate:** `libc 0.2` (`src/Cargo.toml: [workspace.dependencies]`)
- **Python deps:** stdlib (`os`, `typing`; `importlib.machinery` / `importlib.util` / `sys` imported lazily inside the shim-immune `_real_stdlib_glob_module` loader) plus the in-tree extension `fastglob._core` — **no `subprocess`, no `fcntl`, no `pathlib`** (verified 2026-09-21: 0 hits). The `subprocess`/`pass_fds` transport was removed in 0.1.1.
- **No service deps:** No Redis/Postgres/HTTP (verified `grep -r "redis\|postgres\|http" src/` → no hits)

---

## Component Design (Code Graph Measured)

### 1. Matcher (`src/fastglob/src/matcher.rs:54-569`)

- **Character model:** `decode_chars` maps invalid UTF-8 byte `b` → `0xDC00|b` (surrogateescape, Python parity)
- **Translate:** `compile` mirrors `fnmatch.translate` regex shape `(?:(?s: PREFIX (?>.*?F) ... )\Z)` as deterministic IR (`Program{prefix, blocks, tail}`)
- **Class:** `translate_class` parses `[abc]`, `[a-z]`, `[!abc]` with `re._parser` L555-640 semantics (`matcher.rs:273-405`)
- **Measured:** differential vs `fnmatch.fnmatchcase` is committed and reproducible in `tests/test_match_bytes_oracle.py` — 59,860 exhaustive + 20,000 seeded-random str pairs, 4,000 ASCII bytes pairs, **0 mismatches** — plus 6 unit tests `matcher::tests::*`. In **bytes** mode the engine is `os.fsdecode`-consistent and diverges from `fnmatchcase`'s ISO-8859-1 decode for paths containing a valid multi-byte UTF-8 sequence (bounded, pinned; §8.10). Earlier revisions of this line cited a 400k run via `out/dev/diff_match.py`, which is gitignored and absent — an unfalsifiable citation, now replaced by the committed instrument.

### 2. Walker (`src/fastglob/src/walk.rs:1-1717`)

Port of `/usr/lib/python3.12/glob.py` algorithm:

```
_iglob -> _glob1 (filter via matcher) / _glob2 (recursive) -> _rlistdir (recurse) -> _iterdir (listdir)
```

- **Posixpath ports:** `p_split`/`p_join`/`fj`/`rtrim_slashes` byte-exact (`walk.rs:99-167`, `p_split` now handles `"/" → ("/","")`)
- **FS primitives:** `cstr` NUL-truncate fallback, `lexists` `lstat`, `isdir` `stat`, `fd_entry_is_dir` `d_type`→`fstatat` with ELOOP prune (`walk.rs:169-245`)
- **Listdir:** `listdir(dir_fd, dirname, dironly)` 3-way: `openat(fd, cstr(dirname), O_DIRECTORY)` else `openat(fd, ".")` with `dup`+`lseek` fallback (`walk.rs:248-330`), `scan_fd` `fdopendir→readdir→closedir` (`walk.rs:333-368`)
- **Guards:** `pattern.len>8192` or `components>512` → `[]` (`walk.rs:48-51`), NUL defense-in-depth

**Measured:** 10 walk tests `walk::tests::*` (basic, hidden, byte-exact, dir_fd rescan, path_quirks, starstar multiplicity, symlink cycle)

### 3. CLI (`src/fastglob/src/main.rs:1-306`)

- **Args:** `args_os() → OsStringExt::into_vec()` byte-exact (`main.rs:250-252`), no UTF-8 panic (fixed F-001)
- **Parse:** `parse(&[Vec<u8>])` handles `--recursive/--include-hidden/--null/--root-dir/--dir-fd`, rejects `NUL`, `pattern too long`, dual-flag (`main.rs:67-193`)
- **Validate:** `validate_dir_fd` `fstat`+`S_IFDIR` for uniform `exit 2` (`main.rs:234-247`)
- **Output:** `sep = NUL if --null else newline` (`main.rs:267`), broken-pipe ignored
- **Exit:** `0` success or empty, `2` misuse (diagnostics on `stderr`)

**Verified:** `src/target/release/fastglob --help` → `Options:` 5 flags + `escape` (452KB)

### 4. Python Wrapper (`python/fastglob/__init__.py`)

- **Never shadows stdlib:** Separate package `import fastglob`
- **In-process engine (0.1.1):** `fastglob._core` PyO3 module (built by maturin from `src/fastglob` with `--features pyo3`; bindings in `src/fastglob/src/pyo3_ext.rs`) — one call, no per-call process spawn; misuse surfaces as ValueError/RuntimeError mirroring the CLI verdicts; the engine ships inside the wheel (`pip install fastglob` just works)
- **Dir_fd:** passed in-process to the engine and never closed by it (the `dup`+`F_DUPFD`/`pass_fds` PEP 446 dance existed only for the removed child-process transport)
- **Bytes:** `os.fsencode(x)`/`fsdecode` surrogateescape in the shim; raw byte records from the engine with bytes-preservation (`_wants_bytes` mirrors CPython `isinstance(pathname, bytes)` typing)
- **API:** `glob`/`iglob`/`escape`/`has_magic` with `bytes|str|PathLike` overloads

---

## Deployment

**Build:** `make build` → `cargo build --release` LTO thin, `opt-level 3`, `strip symbols`, 452KB (`ls -l src/target/release/fastglob` → 463576 bytes, measured 2026-09-21)

**Runtime:** In-process PyO3 `fastglob._core` (no per-call subprocess, no `pass_fds`). No daemon, cache, index, network.

**Transparent layer (DEPLOYED, and stale on this box):** `glob`-named module `shim/glob.py` (0.1.4) injected via `PYTHONPATH=/opt/fastglob-shim` at `bashrc/profile.d/PAM/BASH_ENV`, with a `gnu_glob` escape hatch (order: engine → 139/139 → bench/profile → shim). Deployment date 2026-08-27 for the initial install; the box carries a 2026-09-02 revision that is NOT the current `shim/glob.py` (see README "Deployment drift").

**Deployment driver — `tools/deploy.sh` (`make deploy` / `rollback` / `deploy-status` / `deploy-verify`; remedy R4).** Two artifacts are deployed together because either one alone is half a system: the `fastglob` wheel → site-packages of every interpreter in `PYTHONS`, and `shim/*.py` → `$SHIM_DIR` (a `glob`-named module whose absence silently degrades to stdlib).

- **The gate is a make-level prerequisite of the swap, not advice.** `apply` runs the full local suite (`make test`) first and refuses to touch anything if it is red. Mutating additionally requires `DRY_RUN=0`; the default `DRY_RUN=1` runs the gate, prints the exact plan, and stops.
- **Recoverable by construction.** Before the first mutation, `apply` snapshots *both* artifacts into `$BACKUP_ROOT/<stamp>` (default `/var/backups/fastglob`) and restores that snapshot automatically if any later step fails — verified 2026-09-21 by forcing an install failure and confirming the pre-existing shim was restored byte-exactly.
- **`make deploy-status` is read-only and exits 1 on drift**, so the divergence the README describes is now detectable instead of merely documented. `make deploy-verify` asserts the live environment (shadow resolution, `FASTGLOB_SHIM_LOUD`, `from glob import *`, deployed hash vs repo, installed wheel in isolation).

**Deliberately not automated: the PYTHONPATH injection files.** They belong to the box provisioner (`agent-bin-setup`: `/etc/environment`, `/etc/profile.d/99-fastglob.sh`, `~/.bashrc`, `/opt/agent-bin/bash_noninteractive`) and are re-applied on the next provision, so rewriting them would fight the provisioner. Falling back to stdlib is done by removing the *shadow* instead: an injection path whose directory has no `glob.py` resolves to stdlib `glob` (measured 2026-09-21 — an empty directory and a nonexistent directory both yield `/usr/lib/python3.12/glob.py`). The shim also keeps its own import-time fallback when `import fastglob` fails (engine missing); what no shim-only mechanism can do is notice that fastglob answered *wrongly*, which is the gap the gate closes.

**Which artifact answered?** `import fastglob` is not a reliable probe of the installed wheel: on this box `~/.local/lib/python3.12/site-packages/fastglob.pth` puts the repo source tree ahead of site-packages (measured 2026-09-21: in-place → `/home/ubuntu/fastglob/python/fastglob/__init__.py`, isolated `-I` → `/usr/local/lib/python3.12/dist-packages/fastglob/__init__.py`). The driver therefore checks the installed wheel in isolation and reports both resolutions.

---

## Decisions (ADR style)

**ADR-001: Single-pass Rust engine (no dual-engine)**
- **Context:** Candidates `fd`, `rg --files`, `libc glob()`, Python traversal, Rust walker/matcher
- **Decision:** One Rust engine, one compat contract (AGENTS.md §14)
- **Measured:** Selection table in `out/selection-table.md`, `ba7c443b` probe results

**ADR-002: Byte-exact `&[u8]` end-to-end**
- **Context:** Unix arbitrary bytes via `OsStr`, invalid UTF-8 `0xDC00|b`, `surrogateescape`
- **Decision:** `OsStrExt::as_bytes`/`OsStringExt::from_vec` everywhere, no lossy UTF-8 on hot path (`lib.rs:14`)
- **Consequence:** Handles `weird` filenames (`tests/fixtures/tree/weird`), `byte_exact` tests

**ADR-003: Pattern length guard (8192/512)**
- **Context:** 100KB `a/*` repeated → stack overflow SIGABRT -6 (F-002)
- **Decision:** `main.rs:158-164` + `walk.rs:48-51` reject → `exit 2` / `[]` to bound `_iglob` recursion (attacker-controlled depth, not kernel `SYMLOOP_MAX=40`)

**ADR-004: `args_os` + `fstat` validation**
- **Context:** `env::args().unwrap()` panic on `\xff` (F-001), `999999999` silent empty (F-005)
- **Decision:** `args_os` + `fstat(S_IFDIR)` uniform `exit 2`

---

## Verification

```bash
# File structure
find . -name "*.rs" -o -name "*.py" | grep -v target | sort | head -20
# Expected: src/fastglob/src/lib.rs etc.

# Dependencies
grep -h "dependencies" src/Cargo.toml -A5
# Expected: libc = "0.2"

# Build + test
make test 2>&1 | tail -1
# Expected: 139 executed / 139 passed / 0 failed

# Binary
ls -lh src/target/release/fastglob
# Expected: 387K
```

**Last Verified:** 2026-08-22 23:46 UTC via `cargo test` + `make compat`

---
*Architecture measured from `src/fastglob/src/*.rs` 1921 lines, `docs/final-gate.md §26` required commands, `AGENTS.md` intent.*
