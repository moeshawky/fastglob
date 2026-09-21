# Fast Glob — API Reference

**Source hierarchy:** Running code (highest) → code graph → tests → comments.
**Last Verified:** 2026-08-22 22:22 UTC
**Verification** (re-measured 2026-09-21): `cargo test` **34 passed** (17 lib unit + 7 + 10) + `python3 tests/test_package.py` **27 OK** + `python3 tests/compat/compare.py --self-test` **139/139** (+ zone 3/3)

---

## Rust Library `fastglob` (`src/fastglob/src/lib.rs:1-56`, `Cargo.toml` version 0.1.3, `libc 0.2`)

### `fastglob::glob(pathname, root_dir, dir_fd, recursive, include_hidden) -> Vec<OsString>`

**Source:** `src/fastglob/src/lib.rs:34-46`

```rust
pub fn glob(
    pathname: &OsStr,
    root_dir: Option<&OsStr>,
    dir_fd: Option<i32>,
    recursive: bool,
    include_hidden: bool,
) -> Vec<OsString>
```

- **Inputs:** `pathname: &OsStr` byte-exact pattern, `root_dir: Option<&OsStr>` origin shift, `dir_fd: Option<i32>` directory fd, `recursive: bool`, `include_hidden: bool`
- **Output:** `Vec<OsString>` byte-exact pathnames, duplicates preserved, order unspecified (verify via `Counter`)
- **Errors:** Never panics on misses (`[]`); `ETIMEDOUT` not returned — 10s guard via harness (`tests/compat/run_engine.py:63`)
- **Measured:** 16 unit tests cover `walk.rs:529-778` fixtures

**Executable Example (from `tests/fixtures/cases.json:b01`):**
```rust
use std::ffi::OsStr;
let res = fastglob::glob(OsStr::new("basic/*"), Some(OsStr::new("tests/fixtures/tree")), None, false, false);
assert!(res.len() == 15); // b01 basic/* top entries incl. empty dir
```

**Verification:**
```bash
cargo test --manifest-path src/Cargo.toml -- walk::tests::basic_and_literal -v
# Expected: ok 1 passed
```

### `fastglob::escape(pathname) -> OsString`

**Source:** `src/fastglob/src/lib.rs:61-70`, `matcher.rs:557-569`

```rust
pub fn escape(pathname: &OsStr) -> OsString
```

- **Input:** `&OsStr` arbitrary bytes
- **Output:** `OsString` with `*`→`[*]`, `?`→`[?]`, `[`→`[[]`
- **Verified:** `cargo test matcher::tests::has_magic_and_escape` + `fastglob escape "a*b"` → `a[*]b`

**Executable:**
```rust
assert_eq!(fastglob::escape(OsStr::new("a*b")), OsString::from("a[*]b"));
```

### `fastglob::has_magic(s) -> bool`

**Source:** `src/fastglob/src/lib.rs:70-72`, `matcher.rs:547-549`

```rust
pub fn has_magic(s: &OsStr) -> bool
```

- **Output:** `true` if contains `*?[` (`([*?[])` regex)

### `fastglob::walk::glob(pattern, root_dir, dir_fd, opts) -> Vec<Vec<u8>>`

**Source:** `src/fastglob/src/walk.rs:35-52`

```rust
pub fn glob(pattern: &[u8], root_dir: &[u8], dir_fd: Option<i32>, opts: Opts) -> Vec<Vec<u8>>
```

- **Inputs:** `pattern: &[u8]` byte-exact, `root_dir: &[u8]` empty=center, `dir_fd`, `Opts{recursive, include_hidden}`
- **Limits:** `pattern.len() <= 8192`, components `<=512` else `[]` (`walk.rs:44-50` defense-in-depth, CLI rejects `exit 2`)
- **Output:** `Vec<Vec<u8>>` duplicates preserved
- **Verification:** `walk.rs:90` `p_split` byte-exact vs `posixpath.split`

### `fastglob::walk::Opts`

**Source:** `src/fastglob/src/walk.rs:22-29`

```rust
pub struct Opts { pub recursive: bool, pub include_hidden: bool }
```

### `fastglob::matcher::compile / matches / escape`

**Source:** `src/fastglob/src/matcher.rs:54-569`

- `decode_chars(bytes: &[u8]) -> Vec<u32>` surrogateescape `0xDC00|b` (`matcher.rs:54`). Note: this is the `os.fsdecode` model, shared with the `glob()` walk, and is deliberately NOT `fnmatch`'s ISO-8859-1 bytes model — see `docs/compatibility-contract.md` §8.10.
- `compile(pat: &[u8]) -> Program` fnmatch-3.12 translate port (`matcher.rs:208`), always succeeds (unclosed `[` → literal)
- `matches(prog: &Program, name: &[u8]) -> bool` atomic `(?>.*?F)` semantics; differential vs stdlib `fnmatch.fnmatchcase` is committed and reproducible in `tests/test_match_bytes_oracle.py` (59,860 exhaustive + 20,000 random str pairs, 0 mismatches)
- `has_magic / escape` as above

**Verification:** `cargo test matcher::tests::* 6 passed`

---

## Python Package `fastglob` (`python/fastglob/__init__.py`, `__version__ 0.1.3`, PyO3 in-process `_core`)

**Install:** `pip install -e python` or `PYTHONPATH=python` (**verified** 2026-08-22 `PYTHONPATH=python python3 -c 'import fastglob; print(fastglob.escape("a*b"))'` → `a[*]b`)

### `fastglob.glob(pathname, *, root_dir=None, dir_fd=None, recursive=False, include_hidden=False) -> List[str] | List[bytes]`

**Source:** `python/fastglob/__init__.py:188-247` (typed overloads + impl)

```python
@overload
def glob(pathname: Union[str, os.PathLike], *, root_dir=None, dir_fd=None,
         recursive=False, include_hidden=False) -> List[str]: ...
@overload
def glob(pathname: bytes, *, root_dir=None, dir_fd=None,
         recursive=False, include_hidden=False) -> List[bytes]: ...
```

- **Mechanic (0.1.1):** In-process call to the native engine module `fastglob._core` (PyO3, built by maturin from `src/fastglob` with `--features pyo3`; bindings in `src/fastglob/src/pyo3_ext.rs`) — no subprocess, no F_DUPFD/pass_fds; the caller's `dir_fd` is used in-process and never closed by the engine. `os.fsencode`/`fsdecode` surrogateescape in the shim. Result TYPE follows the PATTERN type (Ct38): bytes pattern → raw bytes records, str/PathLike → fsdecode'd str. Misuse verdicts mirror the CLI: embedded NUL → ValueError (stdlib parity); >8192 bytes or >512 components → RuntimeError "pattern too long"; `dir_fd` not an open directory → RuntimeError "fd is not a directory: N".
- **Verified:** `PYTHONPATH=python python3 -c "import fastglob, glob; print(fastglob.glob('*') == glob.glob('*'))"` in `tests/fixtures/tree` → `Counter` equality via harness 139 cases; str-path behavior unchanged by the bytes work (Counter-equal vs stdlib, VERIFIED 2026-08-22).

**Executable Example (from `README.md:36`):**
```python
import fastglob, pathlib, tempfile, os
# In tests/fixtures/tree/basic: 15 entries
# fastglob.glob("basic/*", root_dir="tests/fixtures/tree") == glob.glob("basic/*", root_dir="tests/fixtures/tree")
```

**Errors:** `RuntimeError` if `_core` returns an error (e.g. fd not a directory), `ValueError` for bad `dir_fd` or NUL (PyO3 arg check)

### Bytes patterns (stdlib parity)

Bytes are preserved end-to-end: **bytes pattern → bytes results**, exactly like
the installed reference implementation.

- **Parity evidence (re-measured 2026-09-21 against CPython 3.12.3, the committed capture's interpreter):**
  `glob.escape(b'a*b') == b'a[*]b'`; `glob.glob(b'*.py')` yields `bytes`
  elements; reference source branches on `isinstance(pathname, bytes)`
  (`Lib/glob.py:224/234/245`). fastglob matches:
  `fastglob.escape(b'a*b') == b'a[*]b'`; `fastglob.glob(b'*')` → `List[bytes]`;
  `list(fastglob.iglob(b'*'))` → `bytes` items.
- **DOCUMENTED anchors (Level A):** the CPython glossary defines path-like
  objects to include `bytes`; `os.fspath`/`os.fsencode` document the
  str↔bytes path duality. Note the `glob` module docs themselves say pathname
  "must be a string containing a path specification" — narrower than the
  implementation; this package follows the implementation's observable
  contract (type preservation), which is what compatibility requires.
- **No lossy round-trip:** engine output is already byte-exact; bytes mode
  skips decoding entirely (`_paths(as_bytes=True)`, `__init__.py:89-107`).
- **Mixed types supported:** only the PATTERN decides str-vs-bytes;
  `root_dir` may be str/bytes/PathLike and `dir_fd` an int independently
  (e.g. `glob(b'*', root_dir='/tmp')` → `List[bytes]`). Unsupported:
  patterns that are neither str, bytes, nor PathLike raise `TypeError`
  (like the stdlib); documented in each docstring rather than guessed.

### `fastglob.iglob(...) -> Iterator[str] | Iterator[bytes]`

**Source:** `python/fastglob/__init__.py:250-296` — one binary call, yields lazily

```python
@overload
def iglob(pathname: Union[str, os.PathLike], *, ...) -> Iterator[str]: ...
@overload
def iglob(pathname: bytes, *, ...) -> Iterator[bytes]: ...
```

**Verification:** `list(fastglob.iglob("**/*.py", recursive=True))` same as `glob` but yields; item type follows pattern type (VERIFIED both types 2026-08-22)

### `fastglob.escape(pathname) -> str | bytes`

**Source:** `python/fastglob/__init__.py:298-327`

```python
@overload
def escape(pathname: Union[str, os.PathLike]) -> str: ...
@overload
def escape(pathname: bytes) -> bytes: ...
# escaping runs on the engine's byte-exact output; bytes results returned raw
```

**Executable:**
```python
assert fastglob.escape("a*b") == "a[*]b"
assert fastglob.escape("[abc]") == "[[]abc]"
assert fastglob.escape(b"a*b") == b"a[*]b"
```

**Performance (Measured):**
- Cold start: `make build` 0.13s incremental (measured 2026-09-21), binary 452KB
- Hot path: walk is single-pass, `d_type` + `fstatat` per entry, no `lstat` for common dirs (`walk.rs:245-260`)
- Throughput: see `bench/results/baseline.md` — run `make bench` (wide 100k 395M tree)

**Errors (from tests):**
- `RuntimeError: fastglob exited 2: fastglob: pattern too long` when `len>8192` or `>512` components
- `RuntimeError: ... --dir-fd: not a valid number: abc` — fd text is not a decimal number (exit 2)
- `RuntimeError: ... --dir-fd: fd out of range: 3000000000 (valid 0..=1073741823)` — overflow/negative/out-of-window (exit 2)
- `RuntimeError: ... --dir-fd: fd is not open or not a directory: 999999999` — closed/never-open fd, fstat EBADF (exit 2)
  (sibling verdict when fd is open but points at a file: `fd is not a directory: {fd}`)
- `ValueError: fastglob: embedded null byte in PATTERN` (and `... in --root-dir` when the NUL is in `root_dir`) — raised by the in-process `fastglob._core` PyO3 call at `python/fastglob/__init__.py:141`, **not** a subprocess arg check. The exception TYPE matches stdlib; the message text does not (measured 2026-09-21).

**Verification Commands:**
```bash
cargo test --manifest-path src/Cargo.toml 2>&1 | grep "17 passed"
PYTHONPATH=python python3 -c "import fastglob; print(fastglob.glob('*.py', root_dir='tests/fixtures/tree/basic'))" | head
./src/target/release/fastglob escape "a*b"  # Expected: a[*]b
PYTHONPATH=python python3 -c "import fastglob; assert fastglob.escape(b'a*b')==b'a[*]b'; print('bytes parity ok')"
PYTHONPATH=python python3 -m doctest python/fastglob/__init__.py && echo doctest ok
```

---
*Every API cites `file:line`, every example is executable from `tests/` or `README.md`, every ratio is measured via `cargo test`/`ls -lh`/`time`.*
