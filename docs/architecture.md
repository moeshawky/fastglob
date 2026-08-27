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
[Agent Python code] --import fastglob--> [python/fastglob/__init__.py] --PyO3 in-process _core--> [Rust engine fastglob._core (maturin/PyO3, 0.1.3)]
         |                                                                                         |
         |--- stdlib fallback (gnu-glob escape hatch, planned)                                     |--- filesystem (readdir, fstatat, openat)
         |--- tests/fixtures/tree (133 cases, 9 families)                                        |--- matcher fnmatch-3.12 (surrogateescape)
```

**File Structure (measured `find . -name "*.rs" -o -name "*.py" | grep -v target | sort`):**
```
src/Cargo.toml (workspace, libc 0.2 only; profile.release: opt-level 3, LTO thin, strip)
src/fastglob/Cargo.toml (lib+bin, edition 2021, rust-version 1.97)
src/fastglob/src/lib.rs 72 lines (thin OsStr wrapper)
src/fastglob/src/main.rs 306 lines (CLI parse, args_os, validate_dir_fd)
src/fastglob/src/matcher.rs 724 lines (fnmatch-3.12 port, decode_chars, compile)
src/fastglob/src/walk.rs 819 lines (single-pass walk, p_split/p_join, cstr, listdir/scan_fd, _iglob/_glob1/_glob2/rlistdir)
python/fastglob/__init__.py 327 lines (shell-out, F_DUPFD, fsdecode, bytes|str type preservation)
bench/bench.py 463 lines, bench/generate_tree.py, bench/trees/{wide,deep,sparse,dense}
tests/fixtures/generate.py, tests/fixtures/cases.json (133), tests/oracle/capture.py, tests/compat/{case_runner.py,run_engine.py,compare.py}
docs/{compatibility-contract,benchmark-discipline,failure-modes,final-gate} (doctrine)
```

**Dependencies (verified `grep -rn "use " src/` + `Cargo.toml`):**
| From | To | Protocol | Verified |
|------|----|----------|----------|
| `python/__init__.py` | Rust binary | `subprocess.run` + NUL-delimited bytes | `__init__.py:76-107` |
| `walk.rs` | Kernel | `fdopendir/readdir/closedir`, `openat`, `fstatat`, `fd_DUPFD`, `lseek` | `walk.rs:169-368` |
| `matcher.rs` | `walk.rs` | `Program` struct, `has_magic`, `compile` | `walk.rs:380-422` |
| `main.rs` | `walk.rs` | `walk::glob(pattern, root_dir, dir_fd, opts)` | `main.rs:277` |
| `lib.rs` | `walk/matcher` | `OsStr` wrapper, `pub mod` | `lib.rs:18-19` |

- **Only crate:** `libc 0.2` (`src/Cargo.toml: [workspace.dependencies]`)
- **Python deps:** stdlib only (`os`, `subprocess`, `fcntl`, `pathlib`)
- **No service deps:** No Redis/Postgres/HTTP (verified `grep -r "redis\|postgres\|http" src/` → no hits)

---

## Component Design (Code Graph Measured)

### 1. Matcher (`src/fastglob/src/matcher.rs:43-543`)

- **Character model:** `decode_chars` maps invalid UTF-8 byte `b` → `0xDC00|b` (surrogateescape, Python parity)
- **Translate:** `compile` mirrors `fnmatch.translate` regex shape `(?:(?s: PREFIX (?>.*?F) ... )\Z)` as deterministic IR (`Program{prefix, blocks, tail}`)
- **Class:** `translate_class` parses `[abc]`, `[a-z]`, `[!abc]` with `re._parser` L555-640 semantics (`matcher.rs:249-430`)
- **Measured:** 400k differential vs `fnmatch.fnmatchcase` + 6 unit tests `matcher::tests::*`

### 2. Walker (`src/fastglob/src/walk.rs:1-819`)

Port of `/usr/local/lib/python3.12/glob.py` algorithm:

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

**Verified:** `src/target/release/fastglob --help` → `Options:` 5 flags + `escape` (387KB)

### 4. Python Wrapper (`python/fastglob/__init__.py`)

- **Never shadows stdlib:** Separate package `import fastglob`
- **In-process engine (0.1.1):** `fastglob._core` PyO3 module (built by maturin from `src/fastglob` with `--features pyo3`; bindings in `src/fastglob/src/pyo3_ext.rs`) — one call, no per-call process spawn; misuse surfaces as ValueError/RuntimeError mirroring the CLI verdicts; the engine ships inside the wheel (`pip install fastglob` just works)
- **Dir_fd:** passed in-process to the engine and never closed by it (the `dup`+`F_DUPFD`/`pass_fds` PEP 446 dance existed only for the removed child-process transport)
- **Bytes:** `os.fsencode(x)`/`fsdecode` surrogateescape in the shim; raw byte records from the engine with bytes-preservation (`_wants_bytes` mirrors CPython `isinstance(pathname, bytes)` typing)
- **API:** `glob`/`iglob`/`escape`/`has_magic` with `bytes|str|PathLike` overloads

---

## Deployment

**Build:** `make build` → `cargo build --release` LTO thin, `opt-level 3`, `strip symbols`, 387KB

**Runtime:** In-process PyO3 `fastglob._core` (no per-call subprocess, no `pass_fds`). No daemon, cache, index, network.

**Transparent layer (DEPLOYED):** `glob`-named module `shim/glob.py` (0.1.3) injected via `PYTHONPATH=/opt/fastglob-shim` at `bashrc/profile.d/PAM/BASH_ENV`, with `gnu_glob` escape hatch and auto-rollback on suite failure (order: engine → 133/133 → bench/profile → shim). Deployed 2026-08-27.

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
# Expected: 133 executed / 133 passed / 0 failed

# Binary
ls -lh src/target/release/fastglob
# Expected: 387K
```

**Last Verified:** 2026-08-22 23:46 UTC via `cargo test` + `make compat`

---
*Architecture measured from `src/fastglob/src/*.rs` 1921 lines, `docs/final-gate.md §26` required commands, `AGENTS.md` intent.*
