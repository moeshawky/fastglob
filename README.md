# fastglob

[![CI](https://github.com/moeshawky/fastglob/actions/workflows/ci.yml/badge.svg)](https://github.com/moeshawky/fastglob/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/fastglob.svg)](https://pypi.org/project/fastglob/)
[![Release](https://img.shields.io/github/v/release/moeshawky/fastglob)](https://github.com/moeshawky/fastglob/releases/tag/v0.1.4)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

`fastglob` is a Linux pathname-expansion engine compatible with Python's
installed `glob` contract. It combines a Rust traversal and matcher with a
Python package and an optional standalone CLI. The Python API is intended as a
faster replacement for filesystem-oriented `glob` calls while preserving the
stdlib's path types, options, and observable matching behavior.

This is pathname globbing—not micromatch, gitignore matching, shell brace
expansion, or a string-only matcher such as `oxc fast-glob` or `glob-match`.
The package is separate from the stdlib `glob` module: `import fastglob` does
not replace `import glob`.

**Current release: 0.1.4.** Linux only.

## Install

### From PyPI

```bash
pip install fastglob
```

The `0.1.4` PyPI release is currently **sdist-only**. Installing it builds the
native extension, so a Rust toolchain and `maturin` are required. No prebuilt
wheel is assumed.

### From a source checkout

Requirements:

- Linux
- Rust 1.97 or newer
- Python 3.8 or newer
- `maturin` (installed by the PEP 517 build environment)

Build the release engine and install the editable Python package:

```bash
make build
pip install -e python
```

`make build` produces both the native Python extension used by the package and
the release CLI. The Python engine runs in-process; it does not spawn a helper
process for each call.

### CLI binary only

```bash
make build
./src/target/release/fastglob --help
```

The binary is not installed on `PATH` by a source checkout. Invoke it by this
path or add its containing directory to `PATH` yourself.

## Python API

```python
import fastglob

fastglob.glob("src/**/*.py", recursive=True)
list(fastglob.iglob("*.py"))
fastglob.match("**/vendor/**", "src/vendor/lib.rs")
fastglob.escape("a*b")
fastglob.has_magic("a/*.py")
```

The public functions are `glob`, `iglob`, `match`, `escape`, and `has_magic`.
`translate` is also exposed **only when the running stdlib `glob` exposes it**
(for example, on Python 3.13 and newer); it is not invented on older Python
versions.

### `glob`

```python
fastglob.glob("*.py")
fastglob.glob("**/*.py", recursive=True, include_hidden=True)
fastglob.glob("*.txt", root_dir="docs")
```

`glob` returns a list. `recursive=True` enables the special recursive `**`
component. `root_dir` changes the filesystem origin while results retain the
pattern-built form. `dir_fd` can instead resolve a relative pattern against an
open directory file descriptor; `root_dir` and `dir_fd` cannot be used
together.

The result type follows the pattern type, as in the stdlib:

```python
fastglob.glob("*.py")       # list[str]
fastglob.glob(b"*.py")      # list[bytes]
fastglob.escape("a*b")      # "a[*]b"
fastglob.escape(b"a*b")     # b"a[*]b"
```

`str` results use filesystem decoding with surrogate escapes so arbitrary Linux
filenames can round-trip. Bytes patterns return byte strings.

### `iglob`

```python
for name in fastglob.iglob("**/*.toml", recursive=True):
    print(name)
```

`iglob` keeps the stdlib iterator-shaped API and the name `iglob`, but it is
not streaming in 0.1.4: one engine call materializes the complete result list,
then the iterator yields its items. Peak memory is therefore the same as for
`glob()` for the same pattern.

### `match`

```python
fastglob.match("*.py", "tool.py")                 # True
fastglob.match("**/vendor/**", "src/vendor/lib.rs") # True
fastglob.match("**/vendor/**", "vendor")           # False
fastglob.match("b.rs", "a/b.rs")                   # False
```

`match(pattern, path)` is a whole-path `fnmatch` operation and never accesses
the filesystem. Both `*` and `**` cross `/`; they are not component-aware.
Literal patterns must match the entire path, and a wildcard match needs the
same non-empty behavior as the stdlib matcher. The pattern and path must both
be `str`-like or both be `bytes`-like; mixing the two raises `TypeError`.

The bytes matcher deliberately uses the filesystem's UTF-8 plus surrogate-
escape boundary, while stdlib `fnmatch` uses a latin-1 bytes boundary. Thus
bytes patterns containing valid multibyte UTF-8 can differ from `fnmatch` in
character counts and character classes. This bounded, declared divergence is
described in [the compatibility contract, §8.10](docs/compatibility-contract.md#810-matchpattern-path--bytes-mode-decode-boundary-measured-2026-09-21).

### `escape` and `has_magic`

```python
fastglob.escape("literal[*]?name")  # "literal[[][*]][?]name"
fastglob.has_magic("reports/*.csv") # True
fastglob.has_magic("reports/all.csv") # False
```

`escape` quotes glob metacharacters without filesystem access. `has_magic`
performs a pure pattern scan for `*`, `?`, or `[`. Both preserve the input's
`str`/`bytes` result type.

## CLI

```text
fastglob [OPTIONS] PATTERN
fastglob escape PATTERN
```

Options:

```text
--recursive          enable ** as zero-or-more directories
--include-hidden     allow *, ?, and ** to match dot-prefixed names
--null               delimit output with NUL instead of newline
--root-dir PATH      shift the filesystem origin
--dir-fd N           resolve a relative pattern against open directory fd N
-h, --help           show usage
```

Output is one pathname per line by default. Newlines are valid filename
characters and cannot be round-tripped by newline-delimited output; use
`--null` when consuming arbitrary names:

```bash
./src/target/release/fastglob --null -- '**/*.txt' | while IFS= read -r -d '' path; do
    printf '%s\n' "$path"
done
```

`--root-dir` and `--dir-fd` are mutually exclusive. NUL bytes in `PATTERN` or
`--root-dir` are rejected. Patterns are limited to 8192 bytes and 512 path
components.

Exit codes:

- `0`: successful traversal, including no matches; `--help` also exits 0.
- `1`: runtime output failure other than a downstream broken pipe.
- `2`: argument misuse, invalid directory descriptor, embedded NUL, or an
  over-limit pattern.

## Matching semantics

The traversal contract follows the installed CPython `glob` implementation for
the supported surface:

| Pattern or option | Behavior |
| --- | --- |
| `*` | Matches zero or more characters within one path component; it does not match a leading dot unless the component pattern starts with `.` or `include_hidden=True`. |
| `?` | Matches one character within one path component, with the same hidden-dot rule. |
| `[]` | Character classes and ranges follow the stdlib matcher. `[!abc]` negates a class. |
| `**` | Has recursive meaning only when `recursive=True`; it matches zero or more directories. Otherwise it behaves as a non-recursive wildcard component. |
| `include_hidden=True` | Allows wildcard components to match dot-prefixed names during traversal. |
| Braces | No brace expansion. `{a,b}` is literal pathname text. |

Result ordering is unspecified. Do not depend on directory enumeration order;
when comparing implementations, compare results as multisets rather than
sorted lists or sets. Duplicate results produced by overlapping recursive
expansions are preserved.

For the precise supported contract and error behavior, see
[docs/compatibility-contract.md](docs/compatibility-contract.md) and
[docs/api.md](docs/api.md).

## Declared limits and divergences

These are product behavior, not undocumented promises:

- Linux is the supported operating system.
- `pathlib.Path.glob()` and `.rglob()` remain correct when used with the
  optional compatibility shim, but they are not accelerated by fastglob.
- An embedded NUL produces `ValueError` through the Python API and exit 2 in
  the CLI. The stdlib may instead return an empty result in some cases.
- Recursive walks through symlink cycles terminate. Exact extra cycle-path
  multiplicities are Level C and unspecified; they may differ between the
  fused optimization and the recursive fallback.
- The fused fast path is an optimization for some pattern shapes, including
  patterns such as `*` and `**/*.ext`. It must not change the documented
  non-cycle result multiset.
- `iglob` has iterator-compatible shape but materializes all matches first.
- `match` has the declared bytes/UTF-8 versus stdlib latin-1 boundary described
  above.
- Pattern size is capped at 8192 bytes and 512 components to bound recursion
  and reject crafted pathological inputs.

## Verification

The compatibility oracle is the standard-library `glob` supplied by the
interpreter running the tests. The committed oracle capture was made with
CPython 3.12.3. If your interpreter or execution identity differs, regenerate
the capture before comparing:

```bash
make oracle-capture
```

Run the normal local checks with:

```bash
make build
make test
```

Useful focused checks include:

```bash
cargo test --manifest-path src/Cargo.toml
cargo clippy --workspace --all-targets --all-features --manifest-path src/Cargo.toml -- -D warnings
make compat
```

Compatibility checks compare unspecified-order results as multisets and apply
the documented tolerance only to cycle-redundant paths. The test suite also
covers bytes and `str` behavior, hidden files, recursive expansion, symlinks,
`root_dir`, `dir_fd`, newline-containing names, CLI boundaries, and the
fused-versus-fallback differential.

## Project layout

- `src/fastglob/` — Rust engine and CLI.
- `python/fastglob/` — Python package and PyO3 binding surface.
- `shim/` — optional compatibility layer for applications that need the
  stdlib module name while retaining stdlib fallback behavior.
- `docs/` — API and compatibility documentation.
- `tests/` — unit, package, CLI, and differential tests.

## License

MIT. See [LICENSE](LICENSE).
