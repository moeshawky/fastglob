#!/usr/bin/env python3
"""Shim namespace-parity tests — the shim must be a *satisfiable* `glob`.

Scope: `shim/glob.py`, the deployed transparent shadow for the stdlib `glob`
module. The loudness / routing surface is covered by `tests/test_shim_loud.py`;
this file covers the complementary contract — that shadowing `glob` does not
change what a consumer can *say*.

Why this file exists (regression): the shim adopted the fastglob PACKAGE's
`__all__` verbatim. That list names `match` — a `fastglob` API that stdlib
`glob` does not have and that the shim deliberately does not bind — so
`from glob import *` raised

    AttributeError: module 'glob' has no attribute 'match'

while `import glob` and `glob.glob(...)` kept working. A plain, valid standard
statement became a crash, which breaks the invisibility guarantee the shim
exists to provide. Nothing asserted that `__all__` was satisfiable, so nothing
caught it. These tests do.

Mechanism: each test loads the shim FROM DISK in a fresh interpreter (the shim
is import-time state — `__all__`, `__getattr__`, the engine probe all happen at
module exec), so the file under test is exercised directly, with no engine or
`pip install` required. `PYTHONPATH` places the shim dir ahead of everything and
stdlib is recovered by the shim's own path-strip.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
SHIM_SRC = os.path.join(REPO, "shim", "glob.py")
PY_PKG = os.path.join(REPO, "python")

# Fresh interpreter, shim dir first on PYTHONPATH, repo python/ available so the
# in-process engine is importable. The child prints one JSON blob on stdout.
_PROBE = r"""
import glob, importlib.machinery, importlib.util, json, os, sys

def stdlib_glob():
    '''Load the TRUE stdlib glob (no shim dir on sys.path).'''
    shim = os.path.dirname(os.path.abspath(glob.__file__))
    orig = list(sys.path)
    try:
        sys.path = [p for p in orig
                    if p and os.path.abspath(p) != shim]
        spec = importlib.machinery.PathFinder.find_spec("glob", sys.path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path = orig

out = {"shim_file": glob.__file__, "all": list(getattr(glob, "__all__", []))}
try:
    ns = {}
    exec("from glob import *", ns)
    out["star_import"] = "ok"
    out["star_names"] = sorted(k for k in ns if not k.startswith("__"))
except Exception as e:
    out["star_import"] = f"{type(e).__name__}: {e}"

unbound = []
for name in out["all"]:
    try:
        getattr(glob, name)
    except AttributeError as e:
        unbound.append(f"{name}: {e}")
    except Exception:
        pass
out["unbound_all_names"] = unbound

try:
    std = stdlib_glob()
    out["stdlib_file"] = std.__file__
    out["stdlib_all"] = list(getattr(std, "__all__", []))
except Exception as e:
    out["stdlib_error"] = f"{type(e).__name__}: {e}"

print(json.dumps(out))
"""


def run_probe(*, with_shim_dir: str) -> dict:
    """Exec the shim from `with_shim_dir` in a fresh interpreter.

    Inputs:
        with_shim_dir: directory holding `glob.py` (the shim copy under test).
    Output:
        dict — the probe's JSON payload.
    Errors:
        AssertionError if the child interpreter could not produce the payload;
        its stderr is included in the failure message.
    """
    env = dict(os.environ)
    env.pop("FASTGLOB_SHIM_LOUD", None)
    env["PYTHONPATH"] = os.pathsep.join([with_shim_dir, PY_PKG])
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"shim probe failed rc={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


class ShimNamespaceParity(unittest.TestCase):
    """`import glob` under the shim must remain a satisfiable stdlib `glob`."""

    @classmethod
    def setUpClass(cls) -> None:
        # Test the repo's own shim source, from its real location: the shim
        # strips every directory that holds a shim copy, so running it from
        # its own parent directory is the supported configuration.
        cls.shim_dir = os.path.dirname(SHIM_SRC)
        cls.probe = run_probe(with_shim_dir=cls.shim_dir)
        if os.path.abspath(cls.probe["shim_file"]) != os.path.abspath(SHIM_SRC):
            raise unittest.SkipTest(
                f"another `glob` shadow won the import: {cls.probe['shim_file']}"
            )

    def test_star_import_is_satisfiable(self):
        """`from glob import *` must not raise — it did: AttributeError 'match'.

        This is the regression this file exists for. stdlib answers this
        statement; so must the shim.
        """
        self.assertEqual(
            self.probe["star_import"],
            "ok",
            f"`from glob import *` failed under the shim: {self.probe['star_import']}",
        )

    def test_every_advertised_name_is_bound(self):
        """No name in `__all__` may be un-getattr-able.

        `from glob import *` iterates `__all__` and getattrs each name; one
        dangling entry turns a wildcard import into a crash.
        """
        self.assertEqual(
            self.probe["unbound_all_names"],
            [],
            "`__all__` advertises names the shim never binds:\n  "
            + "\n  ".join(self.probe["unbound_all_names"]),
        )

    def test_all_matches_stdlib_all(self):
        """`__all__` is the compatibility contract: mirror stdlib's exactly.

        Not narrower (a missing name breaks `from glob import *` for a consumer
        that expected it) and not wider (a name stdlib does not export leaks
        into the consumer's namespace via `import *`).
        """
        self.assertIn("stdlib_all", self.probe, f"stdlib load failed: {self.probe}")
        self.assertEqual(
            sorted(self.probe["all"]),
            sorted(self.probe["stdlib_all"]),
            "shim `__all__` diverges from the stdlib contract",
        )

    def test_no_engine_package_names_leak(self):
        """Engine-package APIs (`match`) must not appear as `glob.*` names.

        The shim ACCELERATES `glob`; it does not extend it. `fastglob.match`
        is reached as `fastglob.match`, never as `glob.match`.
        """
        for leaked in ("match",):
            self.assertNotIn(
                leaked,
                self.probe["all"],
                f"engine-package name {leaked!r} leaked into glob.__all__",
            )

    def test_star_import_matches_stdlib_star_import(self):
        """The name set produced by `import *` must equal stdlib's."""
        star = {n for n in self.probe["star_names"] if not n.startswith("__")}
        expected = set(self.probe["stdlib_all"]) | {"__builtins__"}
        # `import *` also binds anything named in __all__ that is not dunder'd;
        # the contract is a subset relation in one direction only — the shim
        # must not leak names stdlib does not export.
        leaked = star - expected
        self.assertEqual(leaked, set(), f"`from glob import *` leaked: {sorted(leaked)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
