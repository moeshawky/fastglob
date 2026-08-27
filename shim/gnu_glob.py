"""gnu_glob — explicit escape hatch, always stdlib glob (never fastglob).

Seamless proxy: re-exports *all* stdlib `glob` attributes (including private
`_StringGlobber`, `_PathGlobber`, `_no_recurse_symlinks`, etc. required by
pathlib in Python 3.14+) so `import gnu_glob` is indistinguishable from
stdlib `glob`.  (Fix for 3.14 `pathlib` ImportError 2026-08-27.)
"""
import sys as _sys

_shim_dir = "/opt/fastglob-shim"
_orig = list(_sys.path)
_mod = None
try:
    _sys.path = [p for p in _orig if p != _shim_dir and p != ""]
    import importlib.machinery as _ilm
    import importlib.util as _iu
    _spec = _ilm.PathFinder.find_spec("glob", _sys.path)
    if _spec and _spec.loader:
        _mod = _iu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)  # type: ignore
    else:
        raise ImportError("gnu_glob: stdlib glob spec not found")
finally:
    _sys.path = _orig

if _mod is None:
    raise ImportError("gnu_glob: stdlib glob spec not found")

# --- seamless proxy: copy all stdlib attributes ---
for _name in dir(_mod):
    if _name.startswith("__") and _name.endswith("__") and _name not in ("__doc__", "__all__"):
        continue
    try:
        globals()[_name] = getattr(_mod, _name)
    except Exception:
        pass

# Truthful diagnostics: report the real stdlib glob path, not this shim's own
# file location (R8).
globals()["__file__"] = _mod.__file__

# Ensure core public API is present (fallback if dir() missed)
try:
    glob = _mod.glob  # type: ignore[no-redef]
except Exception:
    pass
try:
    iglob = _mod.iglob  # type: ignore[no-redef]
except Exception:
    pass
try:
    escape = _mod.escape  # type: ignore[no-redef]
except Exception:
    pass
try:
    has_magic = getattr(_mod, "has_magic", lambda p: False)  # type: ignore[no-redef]
except Exception:
    pass
try:
    translate = getattr(_mod, "translate", None)  # type: ignore[no-redef]
except Exception:
    pass
try:
    __all__ = getattr(_mod, "__all__", ["glob", "iglob", "escape"])  # type: ignore[no-redef]
except Exception:
    pass
try:
    __doc__ = _mod.__doc__  # type: ignore[no-redef]
except Exception:
    pass

# PEP 562 fallback for future attributes
_stdlib_glob = _mod  # for __getattr__

def __getattr__(_name: str):  # type: ignore[no-redef]
    try:
        return getattr(_stdlib_glob, _name)
    except AttributeError:
        raise AttributeError(f"module 'gnu_glob' has no attribute {_name!r}") from None

def __dir__():  # type: ignore[no-redef]
    return sorted(set(globals().keys()) | set(dir(_stdlib_glob)))
