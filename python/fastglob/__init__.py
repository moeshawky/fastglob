"""fastglob — Python-compatibility wrapper around the fastglob engine.

This is a SEPARATE package: it never shadows the stdlib ``glob`` module
(``import glob`` still gives you the standard library). Usage:

    import fastglob
    fastglob.glob("*.py", recursive=True, include_hidden=False)
    fastglob.iglob("a/**/b.txt", recursive=True)
    fastglob.escape("a*b")  -> "a[*]b"
    fastglob.escape(b"a*b") -> b"a[*]b"   # bytes pattern -> bytes result

Documented mechanics (keep these in mind when using in hot loops):

* Every call runs IN-PROCESS against the native engine: the compiled module
  ``fastglob._core`` (PyO3, built by maturin from the Rust engine — same
  walk/matcher the CLI uses). No subprocess, no per-call process spawn.
  ``pip install fastglob`` just works; the engine ships inside the package.
* ``dir_fd`` is passed to the engine directly (same process) — the fd is
  used in-place and never closed by the engine; there is no F_DUPFD or
  pass_fds dance (that race window only existed for child processes).
* Result TYPE follows the PATTERN type (stdlib parity — VERIFIED against
  CPython 3.12: ``glob.escape(b'a*b') == b'a[*]b'``, ``glob(b'*.py')``
  yields bytes elements; the reference branches on
  ``isinstance(pathname, bytes)``): str/PathLike patterns give str results
  decoded via ``os.fsdecode`` (surrogateescape), so arbitrary byte
  filenames round-trip exactly; bytes patterns give raw bytes results —
  the engine output is already byte-exact, so bytes mode simply skips
  decoding (no lossy round-trip).
* Misuse verdicts mirror the CLI (docs/compatibility-contract.md):
  embedded NUL in pattern/root_dir -> ValueError (stdlib parity); pattern
  > 8192 bytes or > 512 path components -> RuntimeError "pattern too
  long"; dir_fd not an open directory -> RuntimeError "fd is not a
  directory: {fd}".
"""

from __future__ import annotations

import os
from typing import Iterator, cast, overload

from . import _core

__all__ = ["escape", "glob", "has_magic", "iglob"]

__version__ = "0.1.2"

# Filesystem path arguments: str, bytes, or a PathLike whose ``__fspath__``
# returns str or bytes (stdlib parity — CPython glob accepts all three).
# Bare ``os.PathLike`` would be ``os.PathLike[Any]`` (implicit Any), which
# mypy --strict (disallow_any_generics) rejects.
_PathArg = str | bytes | os.PathLike[str] | os.PathLike[bytes]


def _fs(x: _PathArg) -> bytes:
    return os.fsencode(x)


def _wants_bytes(x: _PathArg) -> bool:
    """True when results for path argument ``x`` must be bytes-typed.

    Mirrors the reference implementation's type preservation (CPython
    Lib/glob.py branches on ``isinstance(pathname, bytes)``; VERIFIED live
    on CPython 3.12: ``glob.escape(b'a*b') == b'a[*]b'``, ``glob(b'*.py')``
    yields bytes elements): plain bytes/bytearray follow bytes; a PathLike
    follows whatever its ``__fspath__`` returns, exactly like ``os.fspath``
    typing. str (and str-fspath PathLike) -> False.
    """
    if isinstance(x, (bytes, bytearray)):
        return True
    if hasattr(x, "__fspath__"):
        return isinstance(os.fspath(x), bytes)
    return False


def _results(items: list[bytes], want_bytes: bool) -> list[str] | list[bytes]:
    """Convert byte-exact engine records to the contract result type.

    Inputs:
        items: raw engine records (byte-exact)
        want_bytes: False (default) -> decode each record via
            ``os.fsdecode`` (surrogateescape), returning List[str];
            True -> return the raw records as List[bytes]. Used when the
            caller's pattern was bytes so results preserve the input type
            (Ct38): no decoding, no lossy round-trip.
    Output:
        List[str] or List[bytes] — one element per engine record
    Errors:
        None (pure split/decode)
    """
    if want_bytes:
        return items
    return [os.fsdecode(x) for x in items]


def _call(
    pathname: _PathArg,
    root_dir: _PathArg | None,
    dir_fd: int | None,
    recursive: bool,
    include_hidden: bool,
    as_bytes: bool = False,
) -> list[str] | list[bytes]:
    """Execute the in-process fastglob engine with the given pattern and options.

    Inputs:
        pathname: glob pattern as str/bytes/PathLike. The RESULT type follows
            the PATTERN type (Ct38): bytes pattern -> List[bytes] (raw engine
            records); str or PathLike -> List[str] via fsdecode.
        root_dir: optional filesystem origin shift (None = cwd). Its type is
            independent of the result type — e.g. root_dir=str with a bytes
            pattern is supported and yields bytes results.
        dir_fd: optional open directory fd to resolve relative patterns against
        recursive: when True, ``**`` matches zero or more directories
        include_hidden: when True, ``*``/``?``/``**`` match dot-prefixed names
    Output:
        Matching pathnames as List[str] or List[bytes], duplicates preserved,
        order unspecified
    Errors:
        RuntimeError if the engine reports a misuse (pattern too long,
            dir_fd not an open directory)
        TypeError if dir_fd is not a valid int
        ValueError if pathname/root_dir is not str/bytes/PathLike (via
            fsencode), if dir_fd is negative or not a valid open fd, or if
            pattern/root_dir contains an embedded NUL
    """
    want_bytes = _wants_bytes(pathname)
    if dir_fd is not None:
        # Validate dir_fd type and value before use (Level A contract).
        if not isinstance(dir_fd, int):
            raise TypeError(f"dir_fd must be int, got {type(dir_fd).__name__}")
        if dir_fd < 0:
            raise ValueError(f"dir_fd must be non-negative, got {dir_fd}")
        # Validate that dir_fd is an open fd (fstat). Directory-ness is
        # re-checked in-process by the engine (RuntimeError there).
        try:
            os.fstat(dir_fd)
        except OSError as e:
            raise ValueError(f"dir_fd {dir_fd} is not a valid open fd: {e}") from e
    pattern = _fs(pathname)
    root = _fs(root_dir) if root_dir is not None else None
    items = _core.glob(pattern, root, dir_fd, recursive, include_hidden)
    return _results(items, want_bytes)


@overload
def glob(
    pathname: str | os.PathLike[str],
    *,
    root_dir: _PathArg | None = None,
    dir_fd: int | None = None,
    recursive: bool = False,
    include_hidden: bool = False,
) -> list[str]: ...


@overload
def glob(
    pathname: bytes | os.PathLike[bytes],
    *,
    root_dir: _PathArg | None = None,
    dir_fd: int | None = None,
    recursive: bool = False,
    include_hidden: bool = False,
) -> list[bytes]: ...


def glob(
    pathname: _PathArg,
    *,
    root_dir: _PathArg | None = None,
    dir_fd: int | None = None,
    recursive: bool = False,
    include_hidden: bool = False,
) -> list[str] | list[bytes]:
    """Return a list of paths matching ``pathname`` (stdlib-glob kwargs).

    Type contract (stdlib parity — VERIFIED against CPython 3.12:
    ``glob.glob(b'*.py')`` yields bytes elements):
        str or PathLike pattern  -> List[str]   (fsdecode/surrogateescape)
        bytes pattern            -> List[bytes] (raw engine bytes)

    Example (result type follows the PATTERN type; root_dir type is
    independent — only the pattern decides str-vs-bytes)::

        fastglob.glob("*.py", root_dir="src")     # ['x.py', ...]   (str)
        fastglob.glob(b"*.py", root_dir=b"src")   # [b'x.py', ...]   (bytes)
        fastglob.glob(b"*", root_dir="/tmp")      # [b'file', ...]   (bytes)

    Not supported (raises TypeError, like the stdlib): patterns that are
    neither str, bytes, nor PathLike.

    Inputs:
        pathname: pattern (str/bytes/PathLike)
        root_dir: optional root directory shift (str/bytes/PathLike or None)
        dir_fd: optional directory fd (int) for relative resolution
        recursive: enable ``**`` zero-or-more-dirs matching
        include_hidden: allow ``*``/``?``/``**`` to match dotfiles
    Output:
        List[str] or List[bytes] — matching pathnames (duplicates preserved,
        order unspecified)
    Errors:
        Propagates RuntimeError/TypeError/ValueError from ``_call``
    """
    return _call(
        pathname,
        root_dir,
        dir_fd,
        recursive,
        include_hidden,
        as_bytes=_wants_bytes(pathname),
    )


@overload
def iglob(
    pathname: str | os.PathLike[str],
    *,
    root_dir: _PathArg | None = None,
    dir_fd: int | None = None,
    recursive: bool = False,
    include_hidden: bool = False,
) -> Iterator[str]: ...


@overload
def iglob(
    pathname: bytes | os.PathLike[bytes],
    *,
    root_dir: _PathArg | None = None,
    dir_fd: int | None = None,
    recursive: bool = False,
    include_hidden: bool = False,
) -> Iterator[bytes]: ...


def iglob(
    pathname: _PathArg,
    *,
    root_dir: _PathArg | None = None,
    dir_fd: int | None = None,
    recursive: bool = False,
    include_hidden: bool = False,
) -> Iterator[str] | Iterator[bytes]:
    """Yield paths matching ``pathname`` (one in-process engine call per iglob).

    Same type contract as ``glob``: str/PathLike pattern yields str items,
    bytes pattern yields bytes items (stdlib parity, VERIFIED on CPython
    3.12). Example::

        next(fastglob.iglob(b"*"))  # -> bytes
        next(fastglob.iglob("*"))   # -> str

    Inputs: same as ``glob``.
    Output: Iterator[str] or Iterator[bytes] — lazily yields each match
    (materialized via one in-process engine call)
    Errors: same as ``glob``
    """
    items = _call(
        pathname,
        root_dir,
        dir_fd,
        recursive,
        include_hidden,
        as_bytes=_wants_bytes(pathname),
    )
    # _call's union return erases the per-element type; the runtime contract
    # (Ct38, test-verified) is that items is all-str or all-bytes.
    return cast("Iterator[str] | Iterator[bytes]", (p for p in items))


@overload
def escape(pathname: str | os.PathLike[str]) -> str: ...


@overload
def escape(pathname: bytes | os.PathLike[bytes]) -> bytes: ...


def escape(pathname: _PathArg) -> str | bytes:
    """Escape all glob special characters (port of ``glob.escape``).

    Type contract (stdlib parity — VERIFIED against CPython 3.12:
    ``glob.escape(b'a*b') == b'a[*]b'``):

        >>> escape('a*b')
        'a[*]b'
        >>> escape(b'a*b')
        b'a[*]b'

    PathLike input follows its ``__fspath__`` typing (str-fspath -> str
    result, bytes-fspath -> bytes result); anything else raises TypeError.
    Escaping always operates on the engine's byte-exact output: bytes
    results are returned raw (no lossy round-trip); str results are decoded
    via fsdecode (surrogateescape).
    """
    out = _core.escape(_fs(pathname))
    if _wants_bytes(pathname):
        return out
    return os.fsdecode(out)


def has_magic(pathname: _PathArg) -> bool:
    """Return True if ``pathname`` contains any glob magic (``*``, ``?``, ``[``).

    Port of the stdlib's internal ``glob.has_magic`` — a pure pattern scan,
    no filesystem access. Accepts str/bytes/PathLike (fsencoded before the
    engine scan); returns a plain bool.

    Inputs:
        pathname: pattern (str/bytes/PathLike)
    Output:
        bool — True when the pattern contains ``*``, ``?``, or ``[``
    Errors:
        TypeError if pathname is not str/bytes/PathLike (via fsencode)
    """
    return _core.has_magic(_fs(pathname))
