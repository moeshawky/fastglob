# fastglob

[![PyPI](https://img.shields.io/pypi/v/fastglob.svg)](https://pypi.org/project/fastglob/)
[![Release](https://img.shields.io/github/v/release/moeshawky/fastglob)](https://github.com/moeshawky/fastglob/releases/tag/v0.1.4)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

fastglob is a Linux pathname-globbing engine that stays compatible with the
installed Python stdlib `glob` contract while moving the work into a Rust
engine plus a Python wrapper.

It is designed for pathname expansion on Linux, not for shell brace expansion,
`gitignore` matching, micromatch-style matching, or a string-only matcher such as
`oxc fast-glob` or `glob-match`. The Python package is separate from the stdlib
`glob` module: `import fastglob` does not replace `import glob`.

Version 0.1.4.

## Install

### PyPI

```bash
pip install fastglob
```

The 0.1.4 release on PyPI is currently sdist-only. Building it needs a Rust
toolchain and `maturin`, so the source distribution is the public install path
at the moment.

### From a source checkout

Requirements:

- Linux
- Rust 1.97+
- Python 3.8+
- `maturin` for the editable build path

```bash
make build
pip install -e python
```

`make build` produces the Rust release binary and the in-process native module
used by the Python package.

### CLI binary

```bash
make build
./src/target/release/fastglob --help
```

The CLI is not installed to `PATH` by a source checkout. Use the local build
path or add `src/target/release` to `PATH` yourself.

## Python API

```python
import fastglob

fastglob.glob("src/**/*.py", recursive=True)
list(fastglob.iglob("*.py"))
fastglob.match("**/vendor/**", "src/vendor/lib.rs")
fastglob.escape("a*b")
fastglob.has_magic("a/*.py")
```

Public API surface:

- `glob(pathname, *, root_dir=None, dir_fd=None, recursive=False, include_hidden=False)`
- `iglob(...)`
- `match(pattern, path)`
- `escape(pathname)`
- `has_magic(pathname)`
- `translate` when the running stdlib `glob` exposes it

### `glob`

```python
fastglob.glob("*.py")
fastglob.glob("**/*.py", recursive=True, include_hidden=True)
fastglob.glob("*.txt", root_dir="docs")
```

`glob` returns a list of matching paths. `recursive=True` enables `**`; it is
not on by default. `root_dir` shifts the filesystem origin while preserving the
pattern-built return values. `dir_fd` resolves relative patterns against an open
directory file descriptor; `root_dir` and `dir_fd` are mutually exclusive.

Type behavior follows the stdlib:

```python
fastglob.glob("*.py")    # list[str]
fastglob.glob(b"*.py")   # list[bytes]
fastglob.escape("a*b")   # "a[*]b"
fastglob.escape(b"a*b")  # b"a[*]b"
```

Str results are decoded with `os.fsdecode`/surrogateescape semantics so arbitrary
Linux filenames round-trip. Bytes patterns return bytes results.

### `iglob`

```python
for name in fastglob.iglob("**/*.toml", recursive=True):
    print(name)
```

`iglob` keeps the stdlib iterator-shaped API and the name `iglob`, but it is not
streaming in 0.1.4. One engine call materializes the full list and then yields
items, so peak memory is the same as `glob()` for the same pattern.

### `match`

```python
fastglob.match("*.py", "tool.py")                    # True
fastglob.match("**/vendor/**", "src/vendor/lib.rs") # True
fastglob.match("**/vendor/**", "vendor")            # False
fastglob.match("b.rs", "a/b.rs")                    # False
```

`match(pattern, path)` is a whole-path `fnmatch` check. It never touches the
filesystem. Both `*` and `**` cross `/`; they are not component-aware.
Literals must match the entire path, and wildcard semantics follow the stdlib
matcher. A pattern and path must both be `str` or both be `bytes`; mixing them
raises `TypeError`.

There is one declared, bounded bytes-mode divergence from stdlib `fnmatch`: in
bytes mode, `fastglob` decodes with the filesystem-style UTF-8 +
surrogateescape boundary, while stdlib `fnmatch` decodes bytes patterns as
ISO-8859-1. This is intentional and documented in
[docs/compatibility-contract.md](docs/compatibility-contract.md).

### `escape` and `has_magic`

```python
fastglob.escape("literal[*]?name")
fastglob.has_magic("reports/*.csv")
```

`escape` quotes glob metacharacters without filesystem access. `has_magic`
checks whether a pattern contains any glob magic characters (`*`, `?`, or `[`)
and makes no filesystem calls.

## CLI

```text
fastglob [OPTIONS] PATTERN
fastglob escape PATTERN
```

Options:

```text
--recursive          enable ** as zero-or-more directories
--include-hidden     allow *, ?, and ** to match dot-prefixed names
--null               NUL-delimit output instead of newline-delimiting it
--root-dir PATH      shift the filesystem origin
--dir-fd N           resolve relative patterns against an open directory fd
-h, --help           show usage
```

Exit codes:

- `0`: success, including no matches
- `1`: runtime output failure other than a downstream broken pipe
- `2`: argument misuse, invalid descriptor, embedded NUL, or an over-limit pattern

Example:

```bash
./src/target/release/fastglob --null -- '**/*.txt'
```

`--root-dir` and `--dir-fd` are mutually exclusive. Embedded NUL bytes in the
pattern or `--root-dir` are rejected. Patterns are bounded to 8192 bytes and 512
path components.

## Semantics and compatibility

The traversal contract tracks the installed CPython `glob` implementation for the
supported surface:

| Feature | Behavior |
| --- | --- |
| `*` | Matches within one path component. Hidden names are excluded unless `include_hidden=True`. |
| `?` | Matches one character within a single component. |
| `[]` | Character classes and negation follow the stdlib behavior. |
| `**` | Only recursive when enabled with `recursive=True`. |
| hidden names | `include_hidden` enables dot-prefixed name matches. |
| braces | No brace expansion. `{a,b}` is literal text. |

Ordering is unspecified. Compare results as multisets, not as sorted lists, and
preserve duplicate results from overlapping recursive expansions.

For the exact compatibility rules and edge cases, see:

- [docs/compatibility-contract.md](docs/compatibility-contract.md)
- [docs/api.md](docs/api.md)

## Declared limits and divergences

These are part of the product contract:

- Linux-only support.
- `pathlib.Path.glob()` and `.rglob()` remain correct under the optional shim,
but are not accelerated by fastglob.
- Embedded NUL in a pattern raises `ValueError` in Python and exits with code 2
  in the CLI.
- Symlink cycles terminate; exact extra cycle-path counts are Level C and
  unspecified.
- The fused fast path is an optimization for some shapes, such as `*` and
  `**/*.ext`; it must not change the documented non-cycle result multiset.
- `iglob` is iterator-shaped but materializes the full list before yielding.
- Bytes-mode `match` intentionally differs from stdlib `fnmatchcase` for valid
  UTF-8 sequences, as documented in
  [docs/compatibility-contract.md](docs/compatibility-contract.md).
- Patterns longer than 8192 bytes or with more than 512 path components are
  rejected to bound recursion depth.

## Verification

The compatibility oracle is the stdlib `glob` from the interpreter running the
suite. The committed oracle capture reflects CPython 3.12.3. If your runtime or
execution environment differs, rebuild the oracle capture before comparing:

```bash
make oracle-capture
```

Then run the normal project checks:

```bash
make build
make test
```

Useful focused checks:

```bash
cargo test --manifest-path src/Cargo.toml
cargo clippy --workspace --all-targets --all-features --manifest-path src/Cargo.toml -- -D warnings
make compat
```

## Project layout

- `src/fastglob/` — Rust engine and CLI
- `python/fastglob/` — Python package and PyO3 extension
- `shim/` — optional compatibility layer for stdlib-style module interception
- `docs/` — API and compatibility docs
- `tests/` — unit, compatibility, CLI, and differential tests

## License

MIT. See [LICENSE](LICENSE).
