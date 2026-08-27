#!/usr/bin/env python3
"""Oracle capture for Fast Glob compatibility harness (docs/compatibility-contract.md §8).

Thin wrapper (W2 unification): all runner mechanics live in the shared
module tests/compat/case_runner.py. This file (a) identifies the INSTALLED
python3 stdlib `glob` at runtime — never hard-coded, re-verified via
import and stamped into _meta — and (b) writes tests/oracle/capture.json:

    python3 tests/oracle/capture.py            # documented command
    make oracle-capture                        # equivalent Makefile target

Guarantees (inherited from case_runner)
---------------------------------------
- ONE interpreter for the whole run (no per-case process startup — that cost
  is not glob-engine cost, per docs/benchmark-discipline.md §10.1).
- 10 s HARD per-call timeout (single shared mechanism: setitimer ITIMER_REAL).
  A timeout is recorded as {"error": {"type": "Timeout", ..., "timed_out":
  true}} — the cycle case (s08) must record terminates / times-out / errors,
  never hang.
- No semantic filtering: results are captured exactly as the oracle returns
  them, including duplicates (re-recorded in `dup_counts` for multiset
  auditing); ordering is documented-unspecified and diagnostic-only.

Schema (list; first element is `_meta`)
---------------------------------------
_meta:  {_meta: true, python, executable, glob_module, glob_all, uid,
         tree, cwd, timeout_s, cases, generated_utc,
         tree_manifest_sha256}
        tree_manifest_sha256 = fixture-tree manifest hash at CAPTURE time
        (tests/fixtures/generate.py manifest()/manifest_hash()); consumed
        by compare.py's freshness gate (maat COHERENCE C15). Additive
        provenance key — record schema untouched.
case:   {case_id, section, kind, pattern, kwargs, note,
         result: [...] | error: {type, message, timed_out: bool},
         + per-kind extras:
           glob/transient: dup_counts {path: n>1}, duration_ms (diagnostic),
                           base_match (for cases with a `base` case id),
                           ops (transient worker audit)
           escape:         escape, glob_escaped
           translate:      exposed, translate}

Exit code 0 on completion (timeouts are data, not failures of the harness).
"""
# Load TRUE stdlib glob — immune to the fastglob shim on sys.path.
# Under PYTHONPATH=/opt/fastglob-shim a naive `import glob` would resolve to
# the shim (the candidate engine), not the oracle. Mirror the shim's own
# path-strip loader (shim/glob.py:26-47) so the captured oracle is always the
# installed interpreter's real stdlib glob.
import importlib.util as _ilu
import importlib.machinery as _ilm
import sys as _sys
_stdlib_glob = None
_shim_dir = "/opt/fastglob-shim"
try:
    _orig_path = list(_sys.path)
    try:
        _sys.path = [p for p in _sys.path if p != _shim_dir and p != ""]
        _spec = _ilm.PathFinder.find_spec("glob", _sys.path)
        if _spec and _spec.loader:
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
            _stdlib_glob = _mod
    finally:
        _sys.path = _orig_path
except Exception:
    _stdlib_glob = None
if _stdlib_glob is None:
    import glob as _stdlib_glob  # type: ignore[no-redef]
_glob = _stdlib_glob
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(HERE, "capture.json")

# bootstrap: import the shared runner (tests/compat) and the fixture
# generator module (tests/fixtures) — the single source of the manifest
# hash algorithm; importing avoids a third copy of it (anti-S22b).
sys.path.insert(0, os.path.join(REPO, "tests", "compat"))
sys.path.insert(0, os.path.join(REPO, "tests", "fixtures"))
import case_runner  # noqa: E402
import generate as fixture_generator  # noqa: E402


class _OracleSubject:
    """Subject binding case_runner to the installed stdlib glob module.

    label="glob" so timeout messages read "glob call exceeded ..." exactly
    as the pre-unification oracle capture did.
    """

    label = "glob"

    @staticmethod
    def glob(pattern, **kwargs):
        return _glob.glob(pattern, **kwargs)

    @staticmethod
    def escape(pattern):
        return _glob.escape(pattern)

    @staticmethod
    def has_translate():
        return hasattr(_glob, "translate")

    @staticmethod
    def translate(pattern):
        return _glob.translate(pattern)


def main():
    # Re-verify the oracle at runtime; never hard-code (AGENTS.md).
    cases = case_runner.prepare()
    meta = {
        "_meta": True,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "glob_module": _glob.__file__,
        "glob_all": list(_glob.__all__),
        "uid": os.getuid(),
        "tree": case_runner.TREE,
        "cwd": os.getcwd(),
        "timeout_s": case_runner.TIMEOUT_S,
        "cases": len(cases),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # freshness stamp for compare.py's gate: manifest of the live tree
        # at capture time (computed AFTER prepare() chdir; paths absolute)
        "tree_manifest_sha256": fixture_generator.manifest_hash(
            fixture_generator.manifest()),
    }
    print(f"oracle: {meta['executable']} {meta['python']} glob={meta['glob_module']} uid={meta['uid']}",
          file=sys.stderr)
    records = [meta] + case_runner.run_all(cases, _OracleSubject())
    with open(OUT, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
        f.write("\n")
    n_err = sum(1 for r in records[1:] if "error" in r)
    n_to = sum(1 for r in records[1:] if r.get("error", {}).get("timed_out"))
    print(f"wrote {OUT}: {len(cases)} cases, {n_err} oracle errors, {n_to} timeouts", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
