"""Native engine module (compiled Rust, PyO3) — type stubs for mypy strict.

Built by maturin from src/fastglob with `--features pyo3` (see pyproject
[tool.maturin]). Byte-exact surface: everything in/out is bytes; the
Python package (fastglob/__init__.py) owns all str/bytes/PathLike typing.
"""

from __future__ import annotations

__version__: str

def glob(
    pattern: bytes,
    root_dir: bytes | None = None,
    dir_fd: int | None = None,
    recursive: bool = False,
    include_hidden: bool = False,
) -> list[bytes]: ...
def iglob(
    pattern: bytes,
    root_dir: bytes | None = None,
    dir_fd: int | None = None,
    recursive: bool = False,
    include_hidden: bool = False,
) -> list[bytes]: ...
def escape(pattern: bytes) -> bytes: ...
def has_magic(pattern: bytes) -> bool: ...
def match(pattern: bytes, path: bytes) -> bool: ...
