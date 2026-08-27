"""fastglob transparent shim — import glob -> fastglob with stdlib fallback.

Deployed at /opt/fastglob-shim/glob.py and injected via PYTHONPATH at every
launch boundary (profile.d, .bashrc, /etc/environment PAM, .bash_noninteractive).
Agents keep calling `import glob` and notice nothing (invisibility guarantee).

Mechanics:
- Tries `import fastglob` (in-process PyO3 _core, 0.1.1+). If available, re-exports
  glob/iglob/escape/has_magic from fastglob (stdlib parity, 133/133).
- On any failure (fastglob missing, engine error), falls back to stdlib glob
  loaded WITHOUT shim on sys.path (avoids recursion via path manipulation).
- Exposes `gnu_glob` attribute and `import gnu_glob` escape hatch (real stdlib).
- Preserves stdlib `__all__` and `__version__` surface where applicable.
- **Seamless proxy:** all other attributes (including private
  `_StringGlobber`, `_no_recurse_symlinks`, `_GlobberBase`, etc. required by
  pathlib in Python 3.14+) are proxied from stdlib via eager copy + PEP 562
  `__getattr__`/`__dir__` fallback, so future stdlib additions never break
  the shim.  (Fix for 3.14 `pathlib` ImportError 2026-08-27.)
"""
from __future__ import annotations

import sys as _sys

# --- load stdlib glob WITHOUT shim on path (escape hatch) ---
_stdlib_glob = None
try:
    import importlib.util as _ilu
    import importlib.machinery as _ilm
    _shim_dir = "/opt/fastglob-shim"
    # find stdlib glob spec excluding shim dir
    _orig_path = list(_sys.path)
    try:
        _sys.path = [p for p in _sys.path if p != _shim_dir and p != ""]
        _spec = _ilm.PathFinder.find_spec("glob", _sys.path)
        if _spec and _spec.loader:
            import importlib.util as _iu2
            _mod = _iu2.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
            _stdlib_glob = _mod
    finally:
        _sys.path = _orig_path
except Exception:
    # last resort: try normal import (may recurse once, but breaks cycle via sys.modules guard)
    try:
        import glob as _stdlib_glob  # type: ignore[no-redef]
    except Exception:
        _stdlib_glob = None  # type: ignore[assignment]

# fallback if stdlib load failed (should never happen on Linux)
if _stdlib_glob is None:
    raise ImportError("fastglob shim: cannot load stdlib glob for fallback")

# expose stdlib as gnu_glob for escape hatch: `import gnu_glob` or `import glob; glob.gnu_glob`
try:
    import sys as _sys2
    _sys2.modules["gnu_glob"] = _stdlib_glob
    gnu_glob = _stdlib_glob
except Exception:
    gnu_glob = _stdlib_glob  # type: ignore

# --- try fastglob engine, else stdlib ---
try:
    import fastglob as _fg  # in-process _core, pip install fastglob
    glob = _fg.glob
    iglob = _fg.iglob
    escape = _fg.escape
    has_magic = _fg.has_magic
    # Only expose translate when it actually exists (fastglob 0.1.2 provides
    # it on 3.13+, stdlib too). On <3.13 neither has it, so leave it undefined
    # so AttributeError propagates — matching real stdlib (do NOT set it to
    # None, which would make hasattr() falsely True).
    _translate = getattr(_fg, "translate", None)
    if _translate is None:
        _translate = getattr(_stdlib_glob, "translate", None)
    if _translate is not None:
        translate = _translate
    __all__ = getattr(_fg, "__all__", getattr(_stdlib_glob, "__all__", ["glob", "iglob", "escape", "has_magic"]))
    __version__ = getattr(_fg, "__version__", "fastglob-shim")
    _engine = "fastglob"

    # --- seamless proxy: eager copy of all stdlib attributes not already overridden ---
    # Keep module dunders from this shim ( __spec__, __file__, __cached__, __loader__, etc.)
    # so the import system stays consistent; copy everything else.
    for _name in dir(_stdlib_glob):
        if _name.startswith("__") and _name.endswith("__"):
            continue
        if _name not in globals():
            try:
                globals()[_name] = getattr(_stdlib_glob, _name)
            except Exception:
                pass

    # PEP 562 fallback for any future attribute not eagerly copied
    def __getattr__(_name: str):  # type: ignore[no-redef]
        try:
            return getattr(_stdlib_glob, _name)
        except AttributeError:
            raise AttributeError(f"module 'glob' has no attribute {_name!r}") from None

    def __dir__():  # type: ignore[no-redef]
        return sorted(set(globals().keys()) | set(dir(_stdlib_glob)))

except Exception as _e:
    # Fallback: mirror stdlib exactly (seamless)
    for _name in dir(_stdlib_glob):
        # keep shim's own dunders except doc/all which should reflect stdlib
        if _name.startswith("__") and _name.endswith("__") and _name not in ("__doc__", "__all__"):
            continue
        try:
            globals()[_name] = getattr(_stdlib_glob, _name)
        except Exception:
            pass
    # ensure essential names (in case dir() missed them)
    try:
        __all__ = _stdlib_glob.__all__  # type: ignore[no-redef]
    except Exception:
        pass
    try:
        __doc__ = _stdlib_glob.__doc__  # type: ignore[no-redef]
    except Exception:
        pass
    _engine = "stdlib"  # type: ignore[no-redef]

    def __getattr__(_name: str):  # type: ignore[no-redef]
        try:
            return getattr(_stdlib_glob, _name)
        except AttributeError:
            raise AttributeError(f"module 'glob' has no attribute {_name!r}") from None

    def __dir__():  # type: ignore[no-redef]
        return sorted(set(globals().keys()) | set(dir(_stdlib_glob)))
    # optional debug: uncomment to trace fallback
    # import warnings; warnings.warn(f"fastglob shim fallback to stdlib: {_e}", RuntimeWarning, stacklevel=2)

# re-export stdlib extras that fastglob shim does not override (e.g. __doc__) if not already set
try:
    if "__doc__" not in globals() or globals()["__doc__"] is None:
        __doc__ = _stdlib_glob.__doc__
except Exception:
    pass
