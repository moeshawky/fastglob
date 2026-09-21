# fastglob

`fastglob` is a Linux-only Python package that accelerates Python `glob`-style
pathname matching with an in-process Rust/PyO3 engine.

## Install

```bash
pip install fastglob
```

The wheel includes the native `fastglob._core` extension. Source installs need
a Rust toolchain and maturin.

## Use

```python
import fastglob

fastglob.glob("**/*.py", recursive=True)
list(fastglob.iglob("*.py"))  # iterator-shaped, eagerly materialized in 0.1.x
fastglob.match("**/vendor/**", "src/vendor/lib.rs")
fastglob.escape("a*b")
```

`**` requires `recursive=True`. The package follows the documented
`glob`/`fnmatch` contracts described in the repository's
[compatibility contract](https://github.com/moeshawky/fastglob/blob/main/docs/compatibility-contract.md).
Recursive walks over directory cycles terminate, but exact duplicate cycle-path
counts are unspecified. This is not micromatch, gitignore matching, shell brace
expansion, or `pathlib` acceleration.

See the [project README](https://github.com/moeshawky/fastglob) and
[API documentation](https://github.com/moeshawky/fastglob/blob/main/docs/api.md)
for the full surface. Licensed under MIT.
